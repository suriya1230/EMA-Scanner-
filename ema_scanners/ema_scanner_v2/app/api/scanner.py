"""
Scanner API endpoints.

GET  /api/scanner              — Main scanner table
GET  /api/scanner/{symbol}     — Single symbol detail
GET  /api/signals              — Recent signals (filtered)
GET  /api/status               — Health + stats
GET  /api/symbols              — Tracked symbols list
GET  /api/candles/{symbol}     — Candles from DB (for backtest — never hits Binance)
POST /api/backfill             — Re-run historical signal backfill
POST /api/backfill/{symbol}    — Backfill a single symbol

All endpoints accept `?market=futures|spot` (default "futures" — matches
pre-existing behavior for every caller that doesn't pass it). Every read
here comes straight from PostgreSQL — nothing in this file ever calls
Binance/CCXT directly.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.models import Signal
from app.schemas.schemas import (
    ExchangeBreakdown,
    ScannerResponse,
    ScannerRow,
    SignalOut,
    StatusResponse,
    SymbolInfo,
)
from app.services.repository import CandleRepository, SignalRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["scanner"])

VALID_INTERVALS = {"1m", "15m", "1h", "2h", "4h", "6h"}
VALID_MARKETS = {"futures", "spot"}


def get_scanner(market: str = Query("futures", description="futures | spot")):
    """Dependency injection — resolves to the futures or spot ScannerService."""
    if market not in VALID_MARKETS:
        raise HTTPException(status_code=400, detail=f"Invalid market '{market}'. Must be one of: futures, spot")
    from app.main import scanner_service
    from app.services.spot_scanner_service import spot_scanner_service
    return spot_scanner_service if market == "spot" else scanner_service


# ─── Scanner Table ────────────────────────────────────────────────────────────

@router.get("/scanner", response_model=ScannerResponse)
async def get_scanner_table(
    trend: Optional[str] = Query(None, description="Filter by trend: Bullish|Bearish|Neutral"),
    signal: Optional[str] = Query(None, description="Filter by last signal: BUY|SELL"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    scanner=Depends(get_scanner),
):
    """
    Returns the main scanner table data, sorted by 24H volume descending.
    """
    if not scanner.initialized:
        raise HTTPException(status_code=503, detail="Scanner initializing, please retry shortly.")

    rows = await scanner.get_scanner_rows()

    # Apply filters
    if trend:
        rows = [r for r in rows if r["ema_trend"].lower() == trend.lower()]
    if signal:
        rows = [r for r in rows if r.get("last_signal") == signal.upper()]

    total = len(rows)
    page = rows[offset : offset + limit]

    return ScannerResponse(
        data=[ScannerRow(**r) for r in page],
        total=total,
        updated_at=datetime.now(tz=timezone.utc),
    )


# ─── Signals ──────────────────────────────────────────────────────────────────

@router.get("/signals", response_model=list[SignalOut])
async def get_signals(
    symbol: Optional[str] = Query(None),
    signal_type: Optional[str] = Query(None, description="BUY or SELL"),
    interval: str = Query("1h", description="1m | 15m | 1h | 2h | 4h | 6h"),
    market: str = Query("futures", description="futures | spot"),
    limit: int = Query(50, ge=1, le=500),
    days: Optional[int] = Query(None, description="Filter signals from last N days (e.g. 7)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns signals ordered by cross_time descending.
    Use ?interval=2h to get signals for that timeframe (default 1h).
    Use ?market=spot for spot-market signals (default futures).
    Use ?days=7 to get signals from last 7 days.
    Use ?symbol=BTCUSDT to filter by symbol.
    Use ?signal_type=BUY or SELL to filter by type.
    """
    if interval not in VALID_INTERVALS:
        raise HTTPException(status_code=400, detail=f"Invalid interval '{interval}'.")
    if market not in VALID_MARKETS:
        raise HTTPException(status_code=400, detail=f"Invalid market '{market}'.")

    stmt = (
        select(Signal)
        .where(Signal.interval == interval, Signal.market == market)
        .order_by(Signal.cross_time.desc())
        .limit(limit)
    )

    if symbol:
        stmt = stmt.where(Signal.symbol == symbol.upper())
    if signal_type:
        stmt = stmt.where(Signal.signal_type == signal_type.upper())
    if days:
        from datetime import timedelta
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        stmt = stmt.where(Signal.cross_time >= cutoff)

    result = await db.execute(stmt)
    signals = result.scalars().all()
    return [SignalOut.model_validate(s) for s in signals]


# ─── Status ───────────────────────────────────────────────────────────────────

@router.get("/status", response_model=StatusResponse)
async def get_status(
    db: AsyncSession = Depends(get_db),
    scanner=Depends(get_scanner),
):
    """Health check + scanner statistics."""
    signals_today = await SignalRepository.count_signals_today(db, market=scanner.market)
    return StatusResponse(
        status="ready" if scanner.initialized else "initializing",
        symbols_tracked=len(scanner.symbols),
        signals_today=signals_today,
        uptime_seconds=scanner.uptime,
    )


# ─── Exchange Breakdown ─────────────────────────────────────────────────────────

@router.get("/exchanges", response_model=ExchangeBreakdown)
async def get_exchange_breakdown():
    """How many coins are currently being collected from each exchange."""
    from app.main import collector
    return ExchangeBreakdown(**collector.exchange_breakdown)


# ─── Symbols ──────────────────────────────────────────────────────────────────

@router.get("/symbols", response_model=list[SymbolInfo])
async def get_symbols(scanner=Depends(get_scanner)):
    """Returns all tracked symbols with their basic info."""
    return [
        SymbolInfo(**info)
        for info in scanner.symbol_info.values()
    ]


# ─── Per-Symbol Detail ────────────────────────────────────────────────────────

@router.get("/scanner/{symbol}", response_model=ScannerRow)
async def get_symbol_detail(
    symbol: str,
    scanner=Depends(get_scanner),
):
    """Returns scanner row for a single symbol."""
    symbol = symbol.upper()
    if not scanner.initialized:
        raise HTTPException(status_code=503, detail="Scanner initializing.")

    state = scanner.states.get(symbol)
    if not state:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not tracked.")

    rows = await scanner.get_scanner_rows()
    row = next((r for r in rows if r["symbol"] == symbol), None)
    if not row:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found.")

    return ScannerRow(**row)


# ─── Backfill ─────────────────────────────────────────────────────────────────

@router.post("/backfill")
async def trigger_backfill(
    background_tasks: BackgroundTasks,
    reset: bool = Query(True, description="Clear existing signals before backfill"),
    interval: Optional[str] = Query(None, description="1m | 15m | 1h | 2h | 4h | 6h — omit to backfill ALL intervals"),
    scanner=Depends(get_scanner),
):
    """
    Re-run historical signal backfill for ALL tracked symbols (for this scanner's market).
    Runs in the background — returns immediately.
    Check /api/status for progress.
    """
    if not scanner.initialized:
        raise HTTPException(status_code=503, detail="Scanner not ready yet.")

    from app.services.backfill import HistoricalBackfill, ALL_INTERVALS

    intervals = [interval] if interval else ALL_INTERVALS

    async def _run():
        bf = HistoricalBackfill()
        for iv in intervals:
            totals = await bf.run(scanner.symbols, reset=reset, interval=iv, market=scanner.market)
            logger.info("Backfill complete (%s %s): %s", scanner.market, iv, totals)
        # Reload in-memory state from Postgres after backfill
        await scanner._rebuild_states()

    background_tasks.add_task(_run)
    return {
        "status": "started",
        "market": scanner.market,
        "symbols": len(scanner.symbols),
        "intervals": intervals,
        "reset": reset,
        "message": "Backfill running in background. Check /api/signals when done.",
    }


@router.post("/backfill/{symbol}")
async def trigger_symbol_backfill(
    symbol: str,
    reset: bool = Query(True, description="Clear existing signals for this symbol first"),
    interval: Optional[str] = Query(None, description="1m | 15m | 1h | 2h | 4h | 6h — omit to backfill ALL intervals"),
    scanner=Depends(get_scanner),
):
    """
    Run historical backfill for a single symbol synchronously.
    Returns immediately with count of signals generated per interval.
    """
    symbol = symbol.upper()
    if not scanner.initialized:
        raise HTTPException(status_code=503, detail="Scanner not ready yet.")
    if symbol not in scanner.symbols:
        raise HTTPException(status_code=404, detail=f"{symbol} not tracked.")

    from app.services.backfill import HistoricalBackfill, ALL_INTERVALS

    intervals = [interval] if interval else ALL_INTERVALS
    bf = HistoricalBackfill()
    signals_generated = {}
    for iv in intervals:
        totals = await bf.run([symbol], reset=reset, interval=iv, market=scanner.market)
        signals_generated[iv] = totals.get(symbol, 0)
    await scanner._rebuild_states()

    return {
        "symbol": symbol,
        "market": scanner.market,
        "signals_generated": signals_generated,
        "reset": reset,
    }


# ─── Candles (serves frontend backtest — reads from DB, never hits Binance) ───

@router.get("/candles/{symbol}")
async def get_candles(
    symbol: str,
    interval: str = Query("1h", description="1m | 15m | 1h | 2h | 4h | 6h"),
    market: str = Query("futures", description="futures | spot"),
    limit: int = Query(900, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns stored candles for a symbol+market+interval directly from
    PostgreSQL — the continuous MarketDataCollector keeps this current, so
    there is no in-memory "live candle" to append here anymore.
    """
    symbol = symbol.upper()

    if interval not in VALID_INTERVALS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid interval '{interval}'. Must be one of: {', '.join(sorted(VALID_INTERVALS))}",
        )
    if market not in VALID_MARKETS:
        raise HTTPException(status_code=400, detail=f"Invalid market '{market}'.")

    candles = await CandleRepository.get_candles(
        db, symbol=symbol, interval=interval, market=market, limit=limit
    )

    if not candles:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No candles found for {symbol} ({market} {interval}). "
                "Symbol may not be tracked or the collector is still catching up."
            ),
        )

    return [
        [
            c.open_time,
            str(c.open),
            str(c.high),
            str(c.low),
            str(c.close),
            str(c.volume),
            c.close_time,
        ]
        for c in candles
    ]
