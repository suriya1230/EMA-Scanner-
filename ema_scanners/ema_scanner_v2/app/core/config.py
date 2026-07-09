from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ema_scanner"

    # Binance
    BINANCE_API_KEY: Optional[str] = None
    BINANCE_API_SECRET: Optional[str] = None
    BINANCE_FUTURES_REST: str = "https://fapi.binance.com"
    BINANCE_FUTURES_WS: str  = "wss://fstream.binance.com"
    BINANCE_SPOT_WS: str     = "wss://stream.binance.com:9443"

    # Bybit / OKX — perpetual-futures universe expansion (fills in coins
    # Binance doesn't list; see app/services/exchange_universe.py)
    BYBIT_REST: str = "https://api.bybit.com"
    # www.okx.com is unreachable from some networks/regions; app.okx.com
    # serves the identical public REST API and is used as the default here.
    OKX_REST: str   = "https://app.okx.com"

    # Scanner
    # COLLECT is set to 0 — no volume filter, every USDT symbol on every
    # exchange gets candles fetched and stored, no matter how illiquid.
    # SIGNAL is the only real cutoff, applied later when building EMA state /
    # generating signals, so raising/lowering it needs no re-backfill — the
    # candle history for every coin is already there.
    MIN_VOLUME_USDT_COLLECT: float = 0.0
    MIN_VOLUME_USDT_SIGNAL: float  = 10_000_000.0
    CANDLES_LIMIT: int     = 3000
    CANDLE_INTERVAL: str   = "1h"

    # EMA periods
    EMA_SHORT: int = 7
    EMA_MID: int   = 25
    EMA_LONG: int  = 99

    # App
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    DEBUG: bool   = False

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
