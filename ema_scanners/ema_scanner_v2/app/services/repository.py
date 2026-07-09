"""
Database repository — all DB reads/writes go through here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Candle, Signal
from app.core.config import settings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Candle Repository  (market+interval aware — spot/futures each store their own candles)
# ─────────────────────────────────────────────────────────────────────────────

class CandleRepository:

    @staticmethod
    async def upsert_candles(session: AsyncSession, rows: list[dict]) -> int:
        """
        Bulk upsert candles. Each row dict must include 'market' and 'interval'.
        Uses constraint: uq_candle_symbol_market_interval_open_time.
        """
        if not rows:
            return 0
        stmt = pg_insert(Candle).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_candle_symbol_market_interval_open_time",
            set_={
                "open":       stmt.excluded.open,
                "high":       stmt.excluded.high,
                "low":        stmt.excluded.low,
                "close":      stmt.excluded.close,
                "volume":     stmt.excluded.volume,
                "close_time": stmt.excluded.close_time,
            },
        )
        await session.execute(stmt)
        return len(rows)

    @staticmethod
    async def upsert_single_candle(session: AsyncSession, candle_dict: dict) -> None:
        await CandleRepository.upsert_candles(session, [candle_dict])

    @staticmethod
    async def get_candles(
        session: AsyncSession,
        symbol: str,
        interval: str = "1h",
        limit: int = settings.CANDLES_LIMIT,
        market: str = "futures",
    ) -> list[Candle]:
        """Return the most-recent `limit` candles for symbol+market+interval, sorted oldest→newest."""
        result = await session.execute(
            select(Candle)
            .where(Candle.symbol == symbol, Candle.market == market, Candle.interval == interval)
            .order_by(Candle.open_time.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    @staticmethod
    async def get_latest_open_time(
        session: AsyncSession,
        symbol: str,
        interval: str = "1h",
        market: str = "futures",
    ) -> int | None:
        result = await session.execute(
            select(Candle.open_time)
            .where(Candle.symbol == symbol, Candle.market == market, Candle.interval == interval)
            .order_by(Candle.open_time.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def count_candles(
        session: AsyncSession,
        symbol: str,
        interval: str = "1h",
        market: str = "futures",
    ) -> int:
        result = await session.execute(
            select(func.count())
            .select_from(Candle)
            .where(Candle.symbol == symbol, Candle.market == market, Candle.interval == interval)
        )
        return result.scalar_one()

    @staticmethod
    async def distinct_symbols(session: AsyncSession, market: str = "futures") -> list[str]:
        """All symbols that have at least one stored candle for this market."""
        result = await session.execute(
            select(Candle.symbol).where(Candle.market == market).distinct()
        )
        return list(result.scalars().all())

    @staticmethod
    async def prune_old_candles(
        session: AsyncSession,
        symbol: str,
        interval: str = "1h",
        keep: int = settings.CANDLES_LIMIT,
        market: str = "futures",
    ) -> None:
        result = await session.execute(
            select(Candle.open_time)
            .where(Candle.symbol == symbol, Candle.market == market, Candle.interval == interval)
            .order_by(Candle.open_time.desc())
            .offset(keep)
            .limit(1)
        )
        cutoff = result.scalar_one_or_none()
        if cutoff is not None:
            await session.execute(
                delete(Candle).where(
                    Candle.symbol == symbol,
                    Candle.market == market,
                    Candle.interval == interval,
                    Candle.open_time <= cutoff,
                )
            )


# ─────────────────────────────────────────────────────────────────────────────
#  Signal Repository  (market+interval aware — spot/futures each store their own signals)
# ─────────────────────────────────────────────────────────────────────────────

class SignalRepository:

    @staticmethod
    async def insert_signal(
        session: AsyncSession,
        symbol: str,
        signal_type: str,
        cross_price: float,
        cross_time: datetime,
        ema_7: float,
        ema_25: float,
        ema_99: float,
        interval: str = "1h",
        market: str = "futures",
    ) -> Signal | None:
        stmt = (
            pg_insert(Signal)
            .values(
                symbol=symbol,
                market=market,
                interval=interval,
                signal_type=signal_type,
                cross_price=cross_price,
                cross_time=cross_time,
                ema_7=ema_7,
                ema_25=ema_25,
                ema_99=ema_99,
            )
            .on_conflict_do_nothing(constraint="uq_signal_symbol_market_interval_time_type")
            .returning(Signal)
        )
        result = await session.execute(stmt)
        row = result.fetchone()
        return row[0] if row else None

    @staticmethod
    async def bulk_insert_signals(session: AsyncSession, rows: list[dict]) -> int:
        """Each row dict must include 'market' and 'interval' keys."""
        if not rows:
            return 0
        stmt = (
            pg_insert(Signal)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_signal_symbol_market_interval_time_type")
        )
        result = await session.execute(stmt)
        return result.rowcount

    @staticmethod
    async def get_last_signal(
        session: AsyncSession, symbol: str, interval: str = "1h", market: str = "futures"
    ) -> Signal | None:
        result = await session.execute(
            select(Signal)
            .where(Signal.symbol == symbol, Signal.market == market, Signal.interval == interval)
            .order_by(Signal.cross_time.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_latest_signal_per_symbol(
        session: AsyncSession,
        symbols: list[str],
        interval: str = "1h",
        market: str = "futures",
    ) -> dict[str, Signal]:
        if not symbols:
            return {}
        result = await session.execute(
            select(Signal)
            .where(
                Signal.symbol.in_(symbols),
                Signal.market == market,
                Signal.interval == interval,
            )
            .order_by(Signal.symbol, Signal.cross_time.desc())
        )
        signals = result.scalars().all()
        seen: dict[str, Signal] = {}
        for sig in signals:
            if sig.symbol not in seen:
                seen[sig.symbol] = sig
        return seen

    @staticmethod
    async def count_signals_today(
        session: AsyncSession, interval: str = "1h", market: str = "futures"
    ) -> int:
        # Use IST (UTC+5:30) for "today" boundary — cross_time is stored in UTC
        # but the frontend displays in IST, so a signal at 06 Jul 03:37 IST
        # = 05 Jul 22:07 UTC. We count signals whose cross_time falls within
        # today's IST date (midnight IST = 18:30 UTC previous day).
        # Scoped to 1H futures by default — the scanner table's "Last Signal"
        # column is 1H-only, so this stat must match what the table actually
        # shows for whichever market's scanner page is asking.
        from datetime import timedelta
        IST_OFFSET = timedelta(hours=5, minutes=30)
        now_ist   = datetime.now(tz=timezone.utc) + IST_OFFSET
        today_ist_midnight = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
        today_utc_cutoff   = today_ist_midnight - IST_OFFSET   # convert back to UTC
        result = await session.execute(
            select(func.count())
            .select_from(Signal)
            .where(
                Signal.cross_time >= today_utc_cutoff,
                Signal.interval == interval,
                Signal.market == market,
            )
        )
        return result.scalar_one()

    @staticmethod
    async def delete_all_signals_for_symbol(
        session: AsyncSession, symbol: str, interval: str = "1h", market: str = "futures"
    ) -> int:
        result = await session.execute(
            delete(Signal).where(
                Signal.symbol == symbol, Signal.market == market, Signal.interval == interval
            )
        )
        return result.rowcount

    @staticmethod
    async def delete_30day_signals_for_symbol(
        session: AsyncSession, symbol: str, interval: str = "1h", market: str = "futures"
    ) -> int:
        from datetime import timedelta
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=30)
        result = await session.execute(
            delete(Signal).where(
                Signal.symbol == symbol,
                Signal.market == market,
                Signal.interval == interval,
                Signal.cross_time >= cutoff,
            )
        )
        return result.rowcount
