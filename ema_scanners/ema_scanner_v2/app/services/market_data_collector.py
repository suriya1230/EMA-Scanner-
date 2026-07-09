"""
Market Data Collection Layer (STEP 1-4 of the market-data redesign).

Continuously fetches candle + 24h ticker data from Binance via CCXT for:
  - SPOT   — rotated across 6 mirror hosts (api.binance.com, api-gcp.binance.com,
             api1-4.binance.com) to combine their independent rate-limit budgets.
  - FUTURES — fapi.binance.com (USDT-M perpetuals), via ccxt's binanceusdm.

This module ONLY fetches and writes candles to PostgreSQL. It never imports
ema_engine.py and never computes an EMA or a signal — that happens entirely
separately, by reading back OUT of PostgreSQL (see backfill.py / scanner_service.py).

Required flow:  Binance -> CCXT Fetcher -> PostgreSQL   (nothing after that here)

Environment note: on this machine, ccxt's default aiohttp DNS resolver
(aiodns/pycares) fails to resolve Binance hostnames at all ("Could not contact
DNS servers"), while a plain synchronous resolver works fine. Every ccxt
exchange instance here is therefore built on a shared aiohttp session using
aiohttp.ThreadedResolver — this is a safe, harmless choice on any platform
(it just uses blocking getaddrinfo() in a thread pool instead of the async
c-ares resolver), so it isn't gated behind an OS check.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from urllib.parse import urlparse

import aiohttp
import ccxt.async_support as ccxt

from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.services.ema_engine import INTERVAL_MS
from app.services.exchange_universe import build_extended_futures_universe
from app.services.repository import CandleRepository

logger = logging.getLogger(__name__)

# ── STEP 1: hosts ───────────────────────────────────────────────────────────

SPOT_HOSTS = [
    "api.binance.com",
    "api-gcp.binance.com",
    "api1.binance.com",
    "api2.binance.com",
    "api3.binance.com",
    "api4.binance.com",
]

# ── STEP 3/4: timeframes + how many candles of each to keep per symbol ─────
# Sized to comfortably cover the backtest page's 30-day window + 99-candle
# EMA warmup for each timeframe, without keeping unbounded history.
CANDLE_RETENTION: dict[str, int] = {
    "1m":  1500,   # ~25 hours
    "15m": 2880,   # 30 days
    "1h":  1500,   # ~62 days
    "2h":  900,    # ~75 days
    "4h":  570,    # ~95 days
    "6h":  450,    # ~112 days
}

BATCH_SIZE = 70              # STEP 3 — symbols per batch
BATCH_WINDOW_SECONDS = 60    # never start more than BATCH_SIZE symbols per this window
HOST_COOLDOWN_SECONDS = 300  # STEP 1 failover — skip an erroring spot host for 5 min

# Per-request candle page size — Binance/Bybit both honor up to 1000, but OKX
# silently caps every response at 300 regardless of what's requested. Using
# the wrong (too-high) limit as the "did we get a full page" check would make
# OKX's pagination stop after just one page, so this must be exchange-aware.
EXCHANGE_PAGE_LIMIT = {"binance": 1000, "bybit": 1000, "okx": 300}

RETRYABLE = (ccxt.NetworkError,)  # covers ExchangeNotAvailable/DDoSProtection/RateLimitExceeded/RequestTimeout


async def _retry(fn, *args, **kwargs):
    """STEP 1 — bounded retries w/ backoff for transient errors."""
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception_type(RETRYABLE),
        reraise=True,
    ):
        with attempt:
            return await fn(*args, **kwargs)


def _market_by_id(markets_by_id: dict, raw_id: str) -> dict | None:
    """ccxt's markets_by_id can map one raw id to a list (multi market-type) or a dict."""
    entry = markets_by_id.get(raw_id)
    if entry is None:
        return None
    return entry[0] if isinstance(entry, list) else entry


class _RotatingSpotClient:
    """STEP 1 — round-robins across the 6 spot hosts, with failover cooldown per host."""

    def __init__(self, session: aiohttp.ClientSession):
        self._exchanges: list[ccxt.binance] = []
        for host in SPOT_HOSTS:
            ex = ccxt.binance({"enableRateLimit": True, "session": session})
            ex.urls["api"]["public"] = f"https://{host}/api/v3"
            self._exchanges.append(ex)
        self._cooldown_until: dict[int, float] = {}
        self._next_idx = 0

    async def load_markets(self):
        """Only one host needs to actually load markets — share the result."""
        last_exc = None
        for ex in self._exchanges:
            try:
                await ex.load_markets()
                for other in self._exchanges:
                    if other is not ex:
                        other.markets = ex.markets
                        other.markets_by_id = ex.markets_by_id
                        other.symbols = ex.symbols
                        other.ids = ex.ids
                return
            except Exception as exc:
                logger.warning("Spot load_markets failed on %s: %s", ex.urls["api"]["public"], exc)
                last_exc = exc
        raise RuntimeError(f"All spot hosts failed to load markets: {last_exc}")

    @property
    def markets(self) -> dict:
        return self._exchanges[0].markets

    @property
    def markets_by_id(self) -> dict:
        return self._exchanges[0].markets_by_id

    def _pick_exchange(self) -> ccxt.binance:
        n = len(self._exchanges)
        now = time.monotonic()
        for _ in range(n):
            idx = self._next_idx
            self._next_idx = (self._next_idx + 1) % n
            if self._cooldown_until.get(idx, 0) <= now:
                return self._exchanges[idx]
        return self._exchanges[self._next_idx]  # all in cooldown — try anyway

    def _mark_failed(self, ex: ccxt.binance):
        idx = self._exchanges.index(ex)
        self._cooldown_until[idx] = time.monotonic() + HOST_COOLDOWN_SECONDS
        logger.warning(
            "Spot host %s failed — cooling down %ds, failing over to next host",
            ex.urls["api"]["public"], HOST_COOLDOWN_SECONDS,
        )

    async def call(self, method: str, *args, **kwargs):
        """STEP 1 — endpoint rotation + failover across all 6 hosts, each attempt retried."""
        last_exc = None
        for _ in range(len(self._exchanges)):
            ex = self._pick_exchange()
            try:
                return await _retry(getattr(ex, method), *args, **kwargs)
            except RETRYABLE as exc:
                self._mark_failed(ex)
                last_exc = exc
        raise last_exc or RuntimeError("All spot hosts exhausted")

    async def close(self):
        for ex in self._exchanges:
            await ex.close()


class MarketDataCollector:
    """
    STEP 1-4: continuously fetches spot + futures candles via CCXT and writes
    them straight to PostgreSQL. No EMA/signal code anywhere in this class.
    """

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._spot: _RotatingSpotClient | None = None
        self._futures: ccxt.binanceusdm | None = None
        self._bybit: ccxt.bybit | None = None     # futures-universe expansion only (see exchange_universe.py)
        self._okx: ccxt.okx | None = None         # same
        self._spot_symbols: list[str] = []       # sorted by 24h volume desc — scanner "rank" order
        self._futures_symbols: list[str] = []
        self._spot_tickers: dict[str, dict] = {}     # raw symbol -> {price, volume_24h, change_24h}
        self._futures_tickers: dict[str, dict] = {}
        self._futures_exchange: dict[str, str] = {}  # futures symbol -> "binance"|"bybit"|"okx"

    @property
    def spot_symbols(self) -> list[str]:
        return self._spot_symbols

    @property
    def futures_symbols(self) -> list[str]:
        return self._futures_symbols

    @property
    def spot_tickers(self) -> dict[str, dict]:
        return self._spot_tickers

    @property
    def futures_tickers(self) -> dict[str, dict]:
        return self._futures_tickers

    @property
    def exchange_breakdown(self) -> dict[str, int]:
        """Per-exchange symbol counts from the most recent filter refresh."""
        counts = Counter(self._futures_exchange.values())
        return {
            "binance_spot": len(self._spot_symbols),
            "binance_futures": counts.get("binance", 0),
            "bybit_futures": counts.get("bybit", 0),
            "okx_futures": counts.get("okx", 0),
            "total_futures": len(self._futures_symbols),
        }

    @property
    def ready(self) -> bool:
        """True once markets are loaded and both clients are usable."""
        return self._spot is not None and self._futures is not None

    async def start(self):
        connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver(), limit=50)
        self._session = aiohttp.ClientSession(connector=connector)
        self._spot = _RotatingSpotClient(self._session)
        self._futures = ccxt.binanceusdm({"enableRateLimit": True, "session": self._session})
        self._bybit = ccxt.bybit({"enableRateLimit": True, "session": self._session})
        self._okx = ccxt.okx({
            "enableRateLimit": True,
            "session": self._session,
            "hostname": urlparse(settings.OKX_REST).netloc,
            "options": {"fetchMarkets": ["swap"]},  # perpetual futures only, per exchange_universe.py
        })

        await self._spot.load_markets()
        await _retry(self._futures.load_markets)

        # Bybit/OKX are additive (futures-universe expansion only) — if either
        # fails to load here, disable just that one and keep going; Binance
        # spot+futures must never be blocked by them.
        try:
            await _retry(self._bybit.load_markets)
        except Exception as exc:
            logger.warning("Bybit load_markets failed — Bybit universe additions disabled this run: %s", exc)
            self._bybit = None
        try:
            await _retry(self._okx.load_markets)
        except Exception as exc:
            logger.warning("OKX load_markets failed — OKX universe additions disabled this run: %s", exc)
            self._okx = None

        logger.info(
            "MarketDataCollector started — %d spot markets, %d futures markets loaded (bybit=%s, okx=%s)",
            len(self._spot.markets), len(self._futures.markets),
            len(self._bybit.markets) if self._bybit else "disabled",
            len(self._okx.markets) if self._okx else "disabled",
        )

    async def stop(self):
        if self._spot:
            await self._spot.close()
        if self._bybit:
            await self._bybit.close()
        if self._okx:
            await self._okx.close()
        if self._futures:
            await self._futures.close()
        if self._session:
            await self._session.close()

    # ── Continuous operation ────────────────────────────────────────────────

    async def run_forever(self):
        await self.start()
        while True:
            try:
                await self._refresh_symbol_filters()
                # Spot and futures hit entirely different hosts/rate-limit
                # domains, so run their batch cycles concurrently — otherwise
                # futures would sit idle waiting for the whole spot cycle
                # (or vice versa) to finish first for no real reason.
                await asyncio.gather(
                    self._collect_cycle("spot", self._spot_symbols),
                    self._collect_cycle("futures", self._futures_symbols),
                )
            except Exception as exc:
                logger.error("Collector cycle error (continuing): %s", exc, exc_info=True)
            await asyncio.sleep(5)

    # ── STEP 2: periodic symbol filtering ───────────────────────────────────

    async def _refresh_symbol_filters(self):
        try:
            tickers = await self._spot.call("fetch_tickers")
            self._spot_symbols, self._spot_tickers = self._filter_symbols(
                tickers, self._spot.markets, market="spot"
            )
            logger.info(
                "Spot scan queue refreshed: %d symbols (volume > %.0f USDT)",
                len(self._spot_symbols), settings.MIN_VOLUME_USDT_COLLECT,
            )
        except Exception as exc:
            logger.error("Spot symbol filter refresh failed: %s", exc)

        try:
            tickers = await _retry(self._futures.fetch_tickers)
            self._futures_symbols, self._futures_tickers = self._filter_symbols(
                tickers, self._futures.markets, market="futures"
            )
            self._futures_exchange = {sym: "binance" for sym in self._futures_symbols}
            logger.info(
                "Futures scan queue refreshed: %d Binance symbols (volume > %.0f USDT)",
                len(self._futures_symbols), settings.MIN_VOLUME_USDT_COLLECT,
            )
        except Exception as exc:
            logger.error("Futures symbol filter refresh failed: %s", exc)
            return  # nothing to extend below if even the Binance universe failed

        # Bybit/OKX universe expansion is fully isolated from the Binance
        # refresh above — any failure here must never touch the Binance
        # symbols/tickers already committed to self._futures_* above.
        try:
            binance_bases = self._all_binance_futures_bases()
            extra_symbols, extra_tickers, extra_exchange = await self._fetch_extended_futures_universe(binance_bases)
            self._futures_symbols = self._futures_symbols + extra_symbols
            self._futures_tickers = {**self._futures_tickers, **extra_tickers}
            self._futures_exchange = {**self._futures_exchange, **extra_exchange}
        except Exception as exc:
            logger.error("Bybit/OKX universe extension failed (Binance universe unaffected): %s", exc)

        by_exchange = Counter(self._futures_exchange.values())
        logger.info(
            "Coin universe — Binance: %d spot, %d futures | Bybit: %d futures | OKX: %d futures | total futures: %d",
            len(self._spot_symbols), by_exchange.get("binance", 0),
            by_exchange.get("bybit", 0), by_exchange.get("okx", 0),
            len(self._futures_symbols),
        )

    def _all_binance_futures_bases(self) -> set[str]:
        """Every base asset Binance lists as a USDT-M perpetual, regardless of
        current 24h volume or active/delisted status.

        This must NOT be `self._futures_symbols` (the volume-filtered scan
        list) — a base can drop below the $10M threshold on Binance while
        still being a live Binance market, and Bybit/OKX often use the exact
        same raw symbol string for the same base (e.g. "SOXLUSDT" on both
        Binance and Bybit). Excluding only the filtered subset would let
        Bybit/OKX candles get written under a symbol Binance still owns,
        silently splicing two exchanges' price history into one row identity.
        """
        return {
            m["base"] for m in self._futures.markets.values()
            if m.get("quote") == "USDT" and m.get("swap") and m.get("linear")
        }

    async def _fetch_extended_futures_universe(
        self, binance_bases: set[str]
    ) -> tuple[list[str], dict[str, dict], dict[str, str]]:
        """Bybit/OKX perpetuals for bases Binance doesn't list (see
        exchange_universe.py). Skips any addition whose exchange client
        failed to load markets in start()."""
        additions = await build_extended_futures_universe(self._session, binance_bases)

        symbols: list[str] = []
        tickers: dict[str, dict] = {}
        exchange_of: dict[str, str] = {}
        for item in additions:
            client = self._bybit if item["exchange"] == "bybit" else self._okx
            if client is None:
                continue
            m = _market_by_id(client.markets_by_id, item["symbol"])
            if not m:
                logger.warning("%s market metadata missing for %s — skipping", item["exchange"], item["symbol"])
                continue
            symbols.append(item["symbol"])
            tickers[item["symbol"]] = {
                "price": item.get("price", 0.0),
                "volume_24h": item["volume"],
                "change_24h": item.get("change_24h", 0.0),
            }
            exchange_of[item["symbol"]] = item["exchange"]
        return symbols, tickers, exchange_of

    @staticmethod
    def _filter_symbols(tickers: dict, markets: dict, market: str) -> tuple[list[str], dict[str, dict]]:
        """Returns (symbols sorted by 24h volume desc, ticker-info-by-symbol)."""
        matched: list[tuple[str, float]] = []
        ticker_info: dict[str, dict] = {}
        for unified_symbol, t in tickers.items():
            m = markets.get(unified_symbol)
            if not m or m.get("quote") != "USDT" or not m.get("active", True):
                continue
            if market == "spot" and not m.get("spot"):
                continue
            if market == "futures" and not (m.get("swap") and m.get("linear")):
                continue
            quote_volume = t.get("quoteVolume") or 0
            if quote_volume < settings.MIN_VOLUME_USDT_COLLECT:
                continue
            raw_id = m["id"]
            matched.append((raw_id, quote_volume))
            ticker_info[raw_id] = {
                "price": t.get("last") or 0.0,
                "volume_24h": quote_volume,
                "change_24h": t.get("percentage") or 0.0,
            }
        matched.sort(key=lambda pair: pair[1], reverse=True)
        symbols = [raw_id for raw_id, _ in matched]
        return symbols, ticker_info

    # ── STEP 3: batch processing (fetch-only, no EMA/signal code) ───────────

    async def _collect_cycle(self, market: str, symbols: list[str]):
        if not symbols:
            return
        for i in range(0, len(symbols), BATCH_SIZE):
            batch = symbols[i : i + BATCH_SIZE]
            batch_start = time.monotonic()
            logger.info("%s batch %d-%d of %d symbols starting", market, i, i + len(batch), len(symbols))

            for symbol in batch:
                for interval in CANDLE_RETENTION:
                    try:
                        await self._fetch_and_store(market, symbol, interval)
                    except Exception as exc:
                        logger.error(
                            "Fetch failed [%s %s %s] (skipping, continuing): %s",
                            market, symbol, interval, exc,
                        )

            elapsed = time.monotonic() - batch_start
            remaining = BATCH_WINDOW_SECONDS - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)

    async def _fetch_and_store(self, market: str, symbol: str, interval: str):
        exchange = self._futures_exchange.get(symbol, "binance") if market == "futures" else "binance"
        candle_ms = INTERVAL_MS[interval]
        markets_by_id = self._markets_by_id_for(market, exchange)
        m = _market_by_id(markets_by_id, symbol)
        if not m:
            logger.warning("No %s market metadata for %s (%s) — skipping", market, symbol, exchange)
            return
        unified = m["symbol"]
        page_limit = EXCHANGE_PAGE_LIMIT[exchange]

        async with AsyncSessionLocal() as session:
            latest_open = await CandleRepository.get_latest_open_time(
                session, symbol, interval=interval, market=market
            )

        rows: list[dict] = []
        if latest_open is None:
            # First time seeing this symbol+interval — page back to the retention target.
            target = CANDLE_RETENTION[interval]
            since = None
            fetched = 0
            # ceil(target / page_limit) + 1 safety round — scales with each
            # exchange's own page size instead of assuming Binance's 1000.
            max_rounds = -(-target // page_limit) + 1
            for _ in range(max_rounds):
                page = await self._fetch_ohlcv(market, exchange, unified, interval, since=since, limit=page_limit)
                if not page:
                    break
                rows.extend(page)
                fetched += len(page)
                since = int(page[-1][0]) + 1
                if fetched >= target or len(page) < page_limit:
                    break
        else:
            # Incremental catch-up — only what's closed since the last stored candle.
            page = await self._fetch_ohlcv(market, exchange, unified, interval, since=latest_open + 1, limit=page_limit)
            rows.extend(page or [])

        if not rows:
            return

        candle_rows = [
            {
                "symbol": symbol,
                "market": market,
                "interval": interval,
                "open_time": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
                "close_time": int(r[0]) + candle_ms - 1,
            }
            for r in rows
        ]

        async with AsyncSessionLocal() as session:
            await CandleRepository.upsert_candles(session, candle_rows)
            await CandleRepository.prune_old_candles(
                session, symbol, interval=interval, market=market, keep=CANDLE_RETENTION[interval]
            )
            await session.commit()

    def _markets_by_id_for(self, market: str, exchange: str) -> dict:
        if market == "spot":
            return self._spot.markets_by_id
        if exchange == "bybit":
            return self._bybit.markets_by_id if self._bybit else {}
        if exchange == "okx":
            return self._okx.markets_by_id if self._okx else {}
        return self._futures.markets_by_id

    async def _fetch_ohlcv(self, market: str, exchange: str, unified_symbol: str, interval: str, since, limit: int):
        if market == "spot":
            return await self._spot.call("fetch_ohlcv", unified_symbol, timeframe=interval, since=since, limit=limit)
        if exchange == "bybit":
            return await _retry(self._bybit.fetch_ohlcv, unified_symbol, timeframe=interval, since=since, limit=limit)
        if exchange == "okx":
            return await _retry(self._okx.fetch_ohlcv, unified_symbol, timeframe=interval, since=since, limit=limit)
        return await _retry(self._futures.fetch_ohlcv, unified_symbol, timeframe=interval, since=since, limit=limit)
