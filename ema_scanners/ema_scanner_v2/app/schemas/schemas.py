from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


# ─── Candle ───────────────────────────────────────────────────────────────────

class CandleBase(BaseModel):
    symbol: str
    market: str
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int


class CandleCreate(CandleBase):
    pass


class CandleOut(CandleBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ─── Signal ───────────────────────────────────────────────────────────────────

class SignalBase(BaseModel):
    symbol: str
    market: str
    interval: str
    signal_type: str
    cross_price: float
    cross_time: datetime
    ema_7: float
    ema_25: float
    ema_99: float


class SignalCreate(SignalBase):
    pass


class SignalOut(SignalBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ─── Scanner Table Row ────────────────────────────────────────────────────────

class ScannerRow(BaseModel):
    rank: int
    symbol: str
    ema_trend: str                      # "Bullish" | "Bearish" | "Neutral"
    price: float
    change_24h: float                   # % change
    volume_24h: float                   # USDT
    last_signal: Optional[str]          # "BUY" | "SELL" | None
    cross_price: Optional[float]
    signal_time: Optional[datetime]
    ema_7: Optional[float]
    ema_25: Optional[float]
    ema_99: Optional[float]


class ScannerResponse(BaseModel):
    data: list[ScannerRow]
    total: int
    updated_at: datetime


# ─── Symbol Info ──────────────────────────────────────────────────────────────

class SymbolInfo(BaseModel):
    symbol: str
    volume_24h: float
    price: float
    change_24h: float


class StatusResponse(BaseModel):
    status: str
    symbols_tracked: int
    signals_today: int
    uptime_seconds: float


# ─── Exchange Breakdown ─────────────────────────────────────────────────────────

class ExchangeBreakdown(BaseModel):
    binance_spot: int
    binance_futures: int
    bybit_futures: int
    okx_futures: int
    total_futures: int
