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
    score: float = 0.0   # 0-100 — see ScannerService._compute_score


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
            score = await self._compute_score(
                symbol=symbol, price=float(closes[-1]),
                ema7=float(ema7[-1]), ema25=float(ema25[-1]), ema99=float(ema99[-1]),
                highs=highs, lows=lows, closes=closes,
                last_signal_type=sig.signal_type if sig else None,
                change_1h=float(change_1h), change_24h=info.get("change_24h", 0.0),
                volume_24h=info.get("volume_24h", 0.0),
            )

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

    # ── Signal Score (0-100) ─────────────────────────────────────────────────
    #
    # Grades the coin's LAST SIGNAL (not the current live trend) — "how
    # strong/still-valid does that BUY or SELL signal look right now, given
    # what price has done since." No last signal at all -> nothing to grade -> 0.
    # Weighted blend of six factors, each already backed by real stored data
    # (no fabricated inputs):
    #   20% EMA separation   — |EMA7-EMA99| as % of price; wider = more decisive trend
    #   20% Higher-TF agree  — does the 4H/6H trend agree with the signal's direction
    #   15% Momentum         — does 1H/24H price change agree with the signal's direction
    #   15% Volatility (ATR) — EMA separation relative to normal 1H noise (ATR14)
    #   15% Volume           — 24H volume as a liquidity proxy
    #   15% Distance/EMA99   — how extended price is from EMA99 (fresh vs exhausted)
    # If price has since reversed against the signal, momentum/higher-TF
    # agreement naturally score low — this is what lets Score fall over time
    # even without a new opposite signal firing.

    HIGHER_TF_INTERVALS = ("4h", "6h")

    async def _compute_score(
        self, symbol: str, price: float, ema7: float, ema25: float, ema99: float,
        highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, last_signal_type: str | None,
        change_1h: float, change_24h: float, volume_24h: float,
    ) -> float:
        if not last_signal_type or price <= 0:
            return 0.0
        direction = 1 if last_signal_type == "BUY" else -1

        ema_sep_pct = abs(ema7 - ema99) / price * 100
        ema_sep_score = min(100.0, (ema_sep_pct / 3.0) * 100)

        atr = self._engine.compute_atr(highs, lows, closes)
        atr_pct = (atr / price * 100) if price > 0 else 0.0
        volatility_score = 50.0 if atr_pct <= 0 else min(100.0, (ema_sep_pct / atr_pct) * 50)

        avg_change = (change_1h + change_24h) / 2
        momentum_score = max(0.0, min(100.0, 50 + (avg_change * direction) * 10))

        agree_count = await self._higher_tf_agreement(symbol, direction)
        higher_tf_score = agree_count * 50.0

        if volume_24h <= settings.MIN_VOLUME_USDT_SIGNAL:
            volume_score = 0.0
        else:
            volume_score = min(100.0, (volume_24h - settings.MIN_VOLUME_USDT_SIGNAL)
                                / (200_000_000 - settings.MIN_VOLUME_USDT_SIGNAL) * 100)

        dist_pct = abs(price - ema99) / ema99 * 100 if ema99 > 0 else 0.0
        if dist_pct < 0.2:
            distance_score = (dist_pct / 0.2) * 60
        elif dist_pct <= 2.0:
            distance_score = 100.0
        elif dist_pct >= 6.0:
            distance_score = 0.0
        else:
            distance_score = 100 - (dist_pct - 2.0) / (6.0 - 2.0) * 100

        score = (
            0.20 * ema_sep_score +
            0.20 * higher_tf_score +
            0.15 * momentum_score +
            0.15 * volatility_score +
            0.15 * volume_score +
            0.15 * distance_score
        )
        return round(max(0.0, min(100.0, score)), 1)

    async def _higher_tf_agreement(self, symbol: str, direction: int) -> int:
        """How many of the 4H/6H trends agree with the last signal's direction (0-2)."""
        agree = 0
        for interval in self.HIGHER_TF_INTERVALS:
            async with AsyncSessionLocal() as session:
                candles = await CandleRepository.get_candles(
                    session, symbol, interval=interval, market=self._market, limit=settings.CANDLES_LIMIT
                )
            if len(candles) < 2:
                continue
            closes = np.array([c.close for c in candles], dtype=float)
            ema7, ema25, ema99 = self._engine.calculate_emas(closes)
            tf_trend = self._engine.classify_trend(float(ema7[-1]), float(ema25[-1]), float(ema99[-1]))
            if (direction == 1 and tf_trend == "Bullish") or (direction == -1 and tf_trend == "Bearish"):
                agree += 1
        return agree

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
            )
            await session.commit()

        if stored:
            # Update in-memory last signal immediately so the scanner table
            # reflects the new signal on the very next /api/scanner poll,
            # without waiting for refresh_states_loop's 30s cycle.
            state.last_signal_type = signal.signal_type
            state.last_cross_price = signal.cross_price
            state.last_signal_time = signal.cross_time
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
