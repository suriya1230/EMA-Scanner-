"""
ScannerService (STEP 5-7 of the market-data redesign) — reads market data
that market_data_collector.py already wrote to PostgreSQL, computes EMAs and
trend for the scanner table, and periodically re-runs the UNCHANGED
HistoricalBackfill to pick up new signals.

This class never calls Binance/CCXT directly — it only ever reads from
PostgreSQL via CandleRepository/SignalRepository. Required flow:
    Binance -> CCXT Fetcher -> PostgreSQL -> EMA Engine (here)
never:
    Binance -> EMA Engine

Parameterized by `market` ("futures" | "spot") so the SAME, unchanged
scanner/EMA/signal logic serves both markets — see spot_scanner_service.py,
which is just this same class instantiated with market="spot".
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import numpy as np

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.services.ema_engine import EMAEngine
from app.services.market_data_collector import MarketDataCollector, CANDLE_RETENTION as BACKTEST_INTERVALS
from app.services.repository import CandleRepository, SignalRepository
from app.services.backfill import HistoricalBackfill
from app.services.signal_score import compute_score, higher_tf_agreement

logger = logging.getLogger(__name__)

STATE_REFRESH_SECONDS = 30   # rebuild in-memory EMA/trend state from DB this often
SIGNAL_SCAN_SECONDS   = 90   # re-run backfill (unchanged logic) for new signals this often


@dataclass
class SymbolState:
    """In-memory EMA/trend snapshot per symbol — rebuilt from Postgres candles."""
    symbol: str
    price: float      = 0.0
    change_1h: float  = 0.0
    change_24h: float = 0.0
    volume_24h: float = 0.0
    ema_7: float  = 0.0
    ema_25: float = 0.0
    ema_99: float = 0.0
    last_signal_type:  str | None    = None
    last_cross_price:  float | None  = None
    last_signal_time:  object | None = None   # datetime | None
    score: float = 0.0   # 0-100, frozen at signal-detection time — see signal_score.py


class ScannerService:
    """
    One instance per market. Initialised once at app startup (after the
    MarketDataCollector has started), then kept fresh by two background
    loops that only ever read from Postgres.
    """

    def __init__(self, market: str = "futures"):
        self._market = market
        self._collector: MarketDataCollector | None = None
        self._states: dict[str, SymbolState] = {}
        self._engine = EMAEngine()
        self._start_time = time.monotonic()
        self._initialized = False

    def attach_collector(self, collector: MarketDataCollector):
        """Wires this scanner to the shared collector so it can read the
        collector's live-filtered symbol list + latest ticker snapshot."""
        self._collector = collector

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def market(self) -> str:
        return self._market

    @property
    def symbols(self) -> list[str]:
        if not self._collector:
            return []
        return self._collector.spot_symbols if self._market == "spot" else self._collector.futures_symbols

    @property
    def symbol_info(self) -> dict[str, dict]:
        tickers = self._tickers()
        return {
            sym: {"symbol": sym, **tickers.get(sym, {"price": 0.0, "volume_24h": 0.0, "change_24h": 0.0})}
            for sym in self.symbols
        }

    def _tickers(self) -> dict[str, dict]:
        if not self._collector:
            return {}
        return self._collector.spot_tickers if self._market == "spot" else self._collector.futures_tickers

    def _signal_symbols(self) -> list[str]:
        """Narrower than `.symbols` — EMA/signal generation only runs for
        symbols at/above MIN_VOLUME_USDT_SIGNAL. The collector still fetches
        and stores candles for the wider MIN_VOLUME_USDT_COLLECT set in
        `.symbols`, so lowering the signal threshold later needs no backfill."""
        tickers = self._tickers()
        return [
            s for s in self.symbols
            if tickers.get(s, {}).get("volume_24h", 0) >= settings.MIN_VOLUME_USDT_SIGNAL
        ]

    @property
    def states(self) -> dict[str, SymbolState]:
        return self._states

    @property
    def uptime(self) -> float:
        return time.monotonic() - self._start_time

    @property
    def initialized(self) -> bool:
        return self._initialized

    # ── Startup ──────────────────────────────────────────────────────────────

    async def initialize(self):
        """
        1. Wait briefly for the collector to have written some 1H candles
           for this market (it may still be mid-cycle on first boot).
        2. Run historical backfill for every interval — UNCHANGED logic,
           just reading whatever the collector has stored so far.
        3. Build initial in-memory EMA/trend state from Postgres.
        """
        logger.info("ScannerService[%s] initializing (Postgres-only)...", self._market)
        await self._wait_for_initial_data()

        backfill = HistoricalBackfill(engine=self._engine)
        for interval in BACKTEST_INTERVALS:
            await backfill.run(self._signal_symbols(), reset=True, interval=interval, market=self._market)

        await self._rebuild_states()
        self._initialized = True
        logger.info(
            "ScannerService[%s] ready — collecting %d symbols, generating signals for %d",
            self._market, len(self.symbols), len(self._signal_symbols()),
        )

    async def _wait_for_initial_data(self, timeout_s: float = 180.0):
        """
        Block briefly so startup backfill has something to scan, without
        ever hanging forever if the collector is unusually slow — the API
        already reports status="initializing" until self._initialized flips.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            symbols = self.symbols
            if symbols:
                async with AsyncSessionLocal() as session:
                    candles = await CandleRepository.get_candles(
                        session, symbols[0], interval="1h", market=self._market, limit=1
                    )
                if candles:
                    return
            await asyncio.sleep(2)
        logger.warning(
            "ScannerService[%s] proceeding without confirmed initial data after %.0fs — "
            "collector may still be catching up.", self._market, timeout_s,
        )

    # ── State rebuild (Postgres -> EMA engine -> in-memory) ─────────────────

    async def _rebuild_states(self):
        """Read latest 1H candles + last 1H signal from Postgres for every
        tracked symbol and (re)build in-memory EMA/trend state. The scanner
        table has always been 1H-based — other intervals are backtest-only."""
        tickers = self._tickers()
        symbols = self._signal_symbols()
        if not symbols:
            return

        async with AsyncSessionLocal() as session:
            sig_map = await SignalRepository.get_latest_signal_per_symbol(
                session, symbols, interval="1h", market=self._market
            )

        for symbol in symbols:
            async with AsyncSessionLocal() as session:
                candles = await CandleRepository.get_candles(
                    session, symbol, interval="1h", market=self._market, limit=settings.CANDLES_LIMIT
                )
            if not candles:
                continue

            closes = np.array([c.close for c in candles], dtype=float)
            highs  = np.array([c.high  for c in candles], dtype=float)
            lows   = np.array([c.low   for c in candles], dtype=float)
            ema7, ema25, ema99 = self._engine.calculate_emas(closes)
            info = tickers.get(symbol, {})
            sig = sig_map.get(symbol)
            change_1h = ((closes[-1] - closes[-2]) / closes[-2] * 100) if len(closes) >= 2 and closes[-2] != 0 else 0.0

            # Score is frozen at signal-detection time (see signal_score.py)
            # and never recomputed against today's live data — just read
            # whatever was stored with the signal. Older rows from before
            # the `score` column existed get computed once here, using the
            # candle AS OF THAT SIGNAL'S OWN cross_time (not "now"), then
            # persisted so this lazy path only ever runs once per row.
            score = 0.0
            if sig is not None:
                if sig.score is not None:
                    score = sig.score
                else:
                    volumes = np.array([c.volume for c in candles], dtype=float)
                    cross_time_ms = int(sig.cross_time.timestamp() * 1000)
                    idx = next(
                        (i for i in range(len(candles)) if candles[i].open_time <= cross_time_ms < candles[i].open_time + 3_600_000),
                        len(candles) - 1,
                    )
                    direction = 1 if sig.signal_type == "BUY" else -1
                    agree = await higher_tf_agreement(symbol, self._market, cross_time_ms, direction, self._engine)
                    score = compute_score(
                        closes, highs, lows, volumes, ema7, ema25, ema99,
                        idx, sig.signal_type, agree, self._engine,
                    )
                    async with AsyncSessionLocal() as session:
                        await SignalRepository.set_signal_score(session, sig.id, score)
                        await session.commit()

            self._states[symbol] = SymbolState(
                symbol=symbol,
                price=info.get("price", 0.0),
                change_1h=float(change_1h),
                change_24h=info.get("change_24h", 0.0),
                volume_24h=info.get("volume_24h", 0.0),
                ema_7=float(ema7[-1]),
                ema_25=float(ema25[-1]),
                ema_99=float(ema99[-1]),
                last_signal_type=sig.signal_type if sig else None,
                last_cross_price=sig.cross_price if sig else None,
                last_signal_time=sig.cross_time if sig else None,
                score=score,
            )

    # ── Live WebSocket Candle Processing (1H only — scanner table) ──────────
    #
    # Real-time path: WebSocket delivers a kline push -> this method writes it
    # to Postgres FIRST -> then re-reads from Postgres before computing EMAs.
    # This keeps the "EMA engine reads only from Postgres" rule intact even
    # for WS-driven updates — the live payload itself is never fed directly
    # into calculate_emas()/detect_signal(). The continuous REST collector
    # still fetches 1H too, as a self-healing fallback if the socket drops.

    async def process_live_candle(self, symbol: str, candle_data: dict, is_closed: bool):
        """
        Called by BinanceWSManager for every 1H kline message for this market.
        Open candle  -> update live price only (instant ticking on the scanner table).
        Closed candle -> persist to DB, then recompute EMA/signal from DB.
        """
        if symbol not in self._states:
            return

        state = self._states[symbol]
        state.price = float(candle_data["close"])

        if not is_closed:
            return

        candle_dict = {
            "symbol": symbol,
            "market": self._market,
            "interval": "1h",
            "open_time": candle_data["open_time"],
            "open": candle_data["open"],
            "high": candle_data["high"],
            "low": candle_data["low"],
            "close": candle_data["close"],
            "volume": candle_data["volume"],
            "close_time": candle_data["close_time"],
        }

        async with AsyncSessionLocal() as session:
            await CandleRepository.upsert_candles(session, [candle_dict])
            await CandleRepository.prune_old_candles(
                session, symbol, interval="1h", market=self._market, keep=settings.CANDLES_LIMIT
            )
            await session.commit()
            candles = await CandleRepository.get_candles(
                session, symbol, interval="1h", market=self._market, limit=settings.CANDLES_LIMIT
            )

        if not candles:
            return

        closes     = np.array([c.close     for c in candles], dtype=float)
        open_times = np.array([c.open_time for c in candles], dtype=np.int64)
        ema7, ema25, ema99 = self._engine.calculate_emas(closes)

        state.ema_7  = float(ema7[-1])
        state.ema_25 = float(ema25[-1])
        state.ema_99 = float(ema99[-1])
        if len(closes) >= 2 and closes[-2] != 0:
            state.change_1h = float((closes[-1] - closes[-2]) / closes[-2] * 100)

        signal = self._engine.detect_signal(ema7, ema25, ema99, open_times)
        if signal is None:
            return

        # Score is computed ONCE, right now, at signal-detection time — using
        # this candle's own data — and frozen onto the row. It is never
        # recomputed later against future/live data (see signal_score.py).
        highs   = np.array([c.high   for c in candles], dtype=float)
        lows    = np.array([c.low    for c in candles], dtype=float)
        volumes = np.array([c.volume for c in candles], dtype=float)
        idx = len(candles) - 1
        direction = 1 if signal.signal_type == "BUY" else -1
        cross_time_ms = int(signal.cross_time.timestamp() * 1000)
        agree = await higher_tf_agreement(symbol, self._market, cross_time_ms, direction, self._engine)
        score = compute_score(closes, highs, lows, volumes, ema7, ema25, ema99, idx, signal.signal_type, agree, self._engine)

        async with AsyncSessionLocal() as session:
            stored = await SignalRepository.insert_signal(
                session,
                symbol=symbol,
                signal_type=signal.signal_type,
                cross_price=signal.cross_price,
                cross_time=signal.cross_time,
                ema_7=signal.ema_7,
                ema_25=signal.ema_25,
                ema_99=signal.ema_99,
                market=self._market,
                score=score,
            )
            await session.commit()

        if stored:
            # Update in-memory last signal immediately so the scanner table
            # reflects the new signal on the very next /api/scanner poll,
            # without waiting for refresh_states_loop's 30s cycle.
            state.last_signal_type = signal.signal_type
            state.last_cross_price = signal.cross_price
            state.last_signal_time = signal.cross_time
            state.score            = score
            logger.info(
                "🚨 [%s] %-4s %-12s | cross_price=%-12.6f cross_time=%s | "
                "EMA7=%.4f EMA25=%.4f EMA99=%.4f",
                self._market, signal.signal_type, symbol,
                signal.cross_price, signal.cross_time.isoformat(),
                signal.ema_7, signal.ema_25, signal.ema_99,
            )

    # ── Background loops — DB-only, no Binance/CCXT calls ───────────────────

    async def refresh_states_loop(self):
        """Periodically rebuild in-memory EMA/trend state from whatever the
        collector has already written to Postgres."""
        while True:
            await asyncio.sleep(STATE_REFRESH_SECONDS)
            try:
                await self._rebuild_states()
            except Exception as exc:
                logger.error(
                    "ScannerService[%s] state refresh error: %s", self._market, exc, exc_info=True
                )

    async def signal_scan_loop(self):
        """
        Periodically re-run the UNCHANGED HistoricalBackfill across every
        interval. reset=False + the DB unique constraint means this only
        ever inserts genuinely new signals, so periodic re-invocation over
        the same candles is safe and idempotent — this is what keeps
        signals current as the collector's continuous fetch loop lands new
        closed candles, without this class ever touching Binance itself.
        """
        backfill = HistoricalBackfill(engine=self._engine)
        while True:
            await asyncio.sleep(SIGNAL_SCAN_SECONDS)
            try:
                for interval in BACKTEST_INTERVALS:
                    await backfill.run(self._signal_symbols(), reset=False, interval=interval, market=self._market)
            except Exception as exc:
                logger.error(
                    "ScannerService[%s] signal scan error: %s", self._market, exc, exc_info=True
                )

    # ── Scanner Table — UNCHANGED logic ─────────────────────────────────────

    async def get_scanner_rows(self) -> list[dict]:
        rows = []
        for rank, symbol in enumerate(self._signal_symbols(), start=1):
            state = self._states.get(symbol)
            if not state:
                continue
            rows.append({
                "rank":        rank,
                "symbol":      symbol,
                "ema_trend":   self._engine.classify_trend(
                                   state.ema_7, state.ema_25, state.ema_99),
                "price":       state.price,
                "score":       state.score,
                "change_1h":   state.change_1h,
                "change_24h":  state.change_24h,
                "volume_24h":  state.volume_24h,
                "ema_7":       state.ema_7,
                "ema_25":      state.ema_25,
                "ema_99":      state.ema_99,
                "last_signal": state.last_signal_type,
                "cross_price": state.last_cross_price,
                "signal_time": state.last_signal_time,
            })
        return rows
