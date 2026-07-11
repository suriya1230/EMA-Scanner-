from datetime import datetime
from sqlalchemy import (
    BigInteger, Column, DateTime, Float, Integer,
    String, UniqueConstraint, func, Index
)
from app.db.database import Base


class Candle(Base):
    __tablename__ = "candles"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    symbol     = Column(String(20), nullable=False, index=True)
    market     = Column(String(10), nullable=False, default="futures")  # spot|futures
    interval   = Column(String(5),  nullable=False, default="1h")  # 1m|15m|1h|2h|4h|6h
    open_time  = Column(BigInteger, nullable=False)
    open       = Column(Float, nullable=False)
    high       = Column(Float, nullable=False)
    low        = Column(Float, nullable=False)
    close      = Column(Float, nullable=False)
    volume     = Column(Float, nullable=False)
    close_time = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # market+interval are part of unique key — same open_time can exist across both
        UniqueConstraint("symbol", "market", "interval", "open_time",
                         name="uq_candle_symbol_market_interval_open_time"),
        Index("ix_candle_symbol_market_interval_open_time", "symbol", "market", "interval", "open_time"),
    )

    def __repr__(self):
        return f"<Candle {self.symbol} {self.market} {self.interval} {self.open_time}>"


class Signal(Base):
    __tablename__ = "signals"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    symbol      = Column(String(20), nullable=False, index=True)
    market      = Column(String(10), nullable=False, default="futures")  # spot|futures
    interval    = Column(String(5),  nullable=False, default="1h")  # 1m|15m|1h|2h|4h|6h
    signal_type = Column(String(4),  nullable=False)       # BUY | SELL
    cross_price = Column(Float,      nullable=False)
    cross_time  = Column(DateTime(timezone=True), nullable=False)
    ema_7       = Column(Float,      nullable=False)
    ema_25      = Column(Float,      nullable=False)
    ema_99      = Column(Float,      nullable=False)
    score       = Column(Float,      nullable=True)  # 0-100, frozen at detection time — see ScannerService._compute_score
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # market+interval are part of unique key — same cross_time can exist across both
        UniqueConstraint(
            "symbol", "market", "interval", "cross_time", "signal_type",
            name="uq_signal_symbol_market_interval_time_type"
        ),
        Index("ix_signal_symbol_created", "symbol", "created_at"),
    )

    def __repr__(self):
        return f"<Signal {self.symbol} {self.market} {self.signal_type} @ {self.cross_time}>"