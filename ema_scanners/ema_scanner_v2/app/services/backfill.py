"""
Historical Signal Backfill
==========================
Scans ALL stored candles for every symbol, for a given market+interval, using
detect_signals() with a 30-day lookback window sized to that interval's
candle span. Reads candles ONLY from PostgreSQL (via CandleRepository) — this
module never talks to Binance/CCXT directly, satisfying the
"EMA engine reads only from PostgreSQL" requirement by construction.

New signal logic (2-candle crossover with strict alignment) — UNCHANGED:
  BUY  signal = EMA7 crosses above EMA25, then EMA99 within 2 candles, alignment EMA7>EMA25>EMA99
  SELL signal = EMA7 crosses below EMA25, then EMA99 within 2 candles, alignment EMA99>EMA25>EMA7
  See ema_engine.py for full rules.

Every valid signal in the last 30 days is inserted as a separate DB record,
tagged with its market+interval. Deduplication is handled by the DB unique
constraint on (symbol, market, interval, signal_type, cross_time).

Called automatically on startup via ScannerService.initialize() and
SpotScannerService.initialize() — once per interval (1m/15m/1h/2h/4h/6h) per
market. Can also be run standalone:

    python -m app.services.backfill
    python -m app.services.backfill BTCUSDT ETHUSDT
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import numpy as np

from app.db.database import AsyncSessionLocal, init_db
from app.services.ema_engine import EMAEngine, INTERVAL_MS, lookback_for_interval
from app.services.repository import CandleRepository, SignalRepository

logger = logging.getLogger(__name__)

ALL_INTERVALS = list(INTERVAL_MS.keys())  # ["1h", "2h", "4h", "6h"]


class HistoricalBackfill:
    """
    Scans stored candles for one interval and inserts all valid crossover
    signals found within the last 30 days (in that interval's own candles).
    """

    def __init__(self, engine: EMAEngine | None = None):
        self._engine = engine or EMAEngine()

    # ── Public entry point ────────────────────────────────────────────────────

    async def run(
        self,
        symbols: list[str],
        reset: bool = True,
        interval: str = "1h",
        market: str = "futures",
    ) -> dict[str, int]:
        """
        Run backfill for all symbols, for one market+interval.

        Args:
            symbols  : e.g. ["BTCUSDT", "ETHUSDT", ...]
            reset    : if True, delete only the 30-day signals before reinserting
                       (keeps signals older than 30 days untouched)
            interval : "1m" | "15m" | "1h" | "2h" | "4h" | "6h"
            market   : "spot" | "futures"

        Returns:
            dict: symbol → number of signals inserted
        """
        lookback = lookback_for_interval(interval)
        logger.info("=" * 64)
        logger.info(
            "Backfill starting — %d symbols | market=%s interval=%s | lookback = %d candles (30 days)",
            len(symbols), market, interval, lookback,
        )
        logger.info("=" * 64)

        totals: dict[str, int] = {}
        grand_total = 0

        for symbol in symbols:
            try:
                count = await self._backfill_symbol(symbol, reset=reset, interval=interval, market=market)
                totals[symbol] = count
                grand_total += count
            except Exception as exc:
                logger.error("Backfill failed for %s %s %s: %s", market, symbol, interval, exc, exc_info=True)
                totals[symbol] = 0

        logger.info("=" * 64)
        logger.info(
            "Backfill complete — %d symbols | market=%s interval=%s | %d total signals inserted",
            len(symbols), market, interval, grand_total,
        )
        logger.info("=" * 64)
        return totals

    # ── Per-symbol logic ──────────────────────────────────────────────────────

    async def _backfill_symbol(self, symbol: str, reset: bool, interval: str, market: str) -> int:
        """
        1. Load all candles for this market+interval (oldest first) — full history for EMA warmup.
        2. Optionally delete existing 30-day signals for this market+interval for a clean rescan.
        3. Calculate EMA7, EMA25, EMA99 across ALL candles.
        4. Call detect_signals() with an interval-sized lookback to find every
           crossover in the last 30 days, using interval-correct interpolation timing.
        5. Bulk-insert all found signals (ON CONFLICT DO NOTHING).
        """
        candle_ms = INTERVAL_MS[interval]
        lookback  = lookback_for_interval(interval)

        async with AsyncSessionLocal() as session:
            candles = await CandleRepository.get_candles(session, symbol, interval=interval, market=market)

            if not candles:
                logger.warning("%s %s %s — no candles in DB, skipping", market, symbol, interval)
                return 0

            if reset:
                deleted = await SignalRepository.delete_30day_signals_for_symbol(
                    session, symbol, interval=interval, market=market
                )
                if deleted:
                    logger.debug("%s %s %s — removed %d old 30-day signals", market, symbol, interval, deleted)
                await session.commit()

        n = len(candles)
        closes     = np.array([c.close     for c in candles], dtype=float)
        open_times = np.array([c.open_time for c in candles], dtype=np.int64)

        logger.info(
            "%s %s %s — %d candles loaded, scanning last %d (30 days)...",
            market, symbol, interval, n, lookback,
        )

        # Calculate EMAs on full history (accurate warmup)
        ema7, ema25, ema99 = self._engine.calculate_emas(closes)

        # Detect every valid signal in the last `lookback` candles
        signal_events = self._engine.detect_signals(
            ema7, ema25, ema99, open_times,
            lookback=lookback, candle_ms=candle_ms,
        )

        if not signal_events:
            logger.info("%s %s %s — 0 signals in last 30 days", market, symbol, interval)
            return 0

        # Build DB rows
        signal_rows = [
            {
                "symbol":      symbol,
                "market":      market,
                "interval":    interval,
                "signal_type": sig.signal_type,
                "cross_price": sig.cross_price,
                "cross_time":  sig.cross_time,
                "ema_7":       sig.ema_7,
                "ema_25":      sig.ema_25,
                "ema_99":      sig.ema_99,
            }
            for sig in signal_events
        ]

        # Log each signal found
        for sig in signal_events:
            logger.info(
                "  %s %s %s %-4s @ %-12.6f  [%s]  EMA7=%.4f EMA25=%.4f EMA99=%.4f",
                market, symbol, interval,
                sig.signal_type,
                sig.cross_price,
                sig.cross_time.strftime("%Y-%m-%d %H:%M UTC"),
                sig.ema_7, sig.ema_25, sig.ema_99,
            )

        async with AsyncSessionLocal() as session:
            inserted = await SignalRepository.bulk_insert_signals(session, signal_rows)
            await session.commit()

        logger.info(
            "%s %s %s — %d crossovers found in 30 days, %d inserted (rest already existed)",
            market, symbol, interval, len(signal_events), inserted,
        )
        return inserted


# ── Standalone runner ─────────────────────────────────────────────────────────

async def _main():
    """python -m app.services.backfill [SYMBOL1 SYMBOL2 ...]

    Backfills ALL intervals (1m/15m/1h/2h/4h/6h) × ALL markets (spot/futures)
    for the given (or all DB-known, per market) symbols.
    """
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    await init_db()
    explicit_symbols = [s.upper() for s in sys.argv[1:]] if len(sys.argv) > 1 else None

    bf = HistoricalBackfill()

    for market in ("futures", "spot"):
        if explicit_symbols is not None:
            symbols = explicit_symbols
        else:
            async with AsyncSessionLocal() as session:
                symbols = sorted(await CandleRepository.distinct_symbols(session, market=market))

        if not symbols:
            logger.info("No %s symbols with stored candles — skipping", market)
            continue

        logger.info("Symbols to backfill [%s]: %d", market, len(symbols))

        for interval in ALL_INTERVALS:
            totals = await bf.run(symbols, reset=True, interval=interval, market=market)

            print(f"\n── Backfill Summary [{market} {interval}] (30-day lookback) ──────────────")
            for sym, cnt in sorted(totals.items(), key=lambda x: -x[1]):
                print(f"  {sym:<15}  {cnt:>4} signals")
            print(f"\n  TOTAL [{market} {interval}]: {sum(totals.values())} signals across {len(totals)} symbols")


if __name__ == "__main__":
    asyncio.run(_main())