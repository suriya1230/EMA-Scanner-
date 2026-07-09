"""
EMA Scanner — FastAPI Application

Startup sequence (market-data redesign — see STEP 1-7):
1. Create DB tables
2. Start MarketDataCollector (CCXT, spot 6-host rotation + futures) —
   continuous fetch-only loop, writes candles straight to PostgreSQL.
   This is the self-healing, eventually-consistent path across ALL
   timeframes for both markets.
3. Initialize ScannerService (futures) and SpotScannerService — both read
   ONLY from PostgreSQL, never from Binance/CCXT directly.
4. Start each scanner's background loops (state refresh + signal scan).
5. Start a 1H WebSocket stream per market (BinanceWSManager) for real-time
   push updates on top of the collector — a REST batch cycle over 100+
   symbols can't match WS latency, so the scanner table's live feel comes
   from here, while the collector remains the fallback if a socket drops.

All done in the FastAPI lifespan context manager.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.db.database import init_db
from app.services.market_data_collector import MarketDataCollector
from app.services.scanner_service import ScannerService
from app.services.spot_scanner_service import spot_scanner_service
from app.services.websocket_manager import BinanceWSManager

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Singleton services (module-level for DI) ─────────────────────────────────

collector = MarketDataCollector()
scanner_service = ScannerService(market="futures")   # default/backward-compatible service


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("EMA Scanner starting up...")
    logger.info("=" * 60)

    # 1. Create database tables
    await init_db()
    logger.info("Database tables created/verified.")

    # 2. Start the continuous market-data collector (fetch-only, CCXT).
    #    Runs forever as a background task — collector.start() (called at the
    #    top of run_forever) loads markets before we wire the scanners to it.
    collector_task = asyncio.create_task(collector.run_forever(), name="market_data_collector")
    # Give the collector a moment to finish start()/load_markets() before the
    # scanners try to read its symbol lists.
    for _ in range(30):
        if collector.ready:
            break
        await asyncio.sleep(1)

    scanner_service.attach_collector(collector)
    spot_scanner_service.attach_collector(collector)

    # 3. Initialize both scanners — reads ONLY from Postgres (never Binance).
    #    Wrapped in try/except: any unexpected failure here must not crash
    #    the whole app — the frontend still needs /api/status to load and
    #    show "initializing" rather than getting a fully dead server.
    try:
        await scanner_service.initialize()
    except Exception:
        logger.exception("ScannerService[futures].initialize() failed unexpectedly.")

    try:
        await spot_scanner_service.initialize()
    except Exception:
        logger.exception("ScannerService[spot].initialize() failed unexpectedly.")

    # 4. Start each scanner's background loops (DB-only — state refresh + signal scan)
    bg_tasks = [
        collector_task,
        asyncio.create_task(scanner_service.refresh_states_loop(), name="futures_state_refresh"),
        asyncio.create_task(scanner_service.signal_scan_loop(), name="futures_signal_scan"),
        asyncio.create_task(spot_scanner_service.refresh_states_loop(), name="spot_state_refresh"),
        asyncio.create_task(spot_scanner_service.signal_scan_loop(), name="spot_signal_scan"),
    ]

    # 5. Start real-time 1H WebSocket streams, one per market
    futures_ws = BinanceWSManager(scanner_service, ws_base=settings.BINANCE_FUTURES_WS, market="futures")
    spot_ws = BinanceWSManager(spot_scanner_service, ws_base=settings.BINANCE_SPOT_WS, market="spot")
    await futures_ws.start()
    await spot_ws.start()

    logger.info("EMA Scanner is ready. 🚀")
    logger.info(
        "Tracking %d futures symbols, %d spot symbols.",
        len(scanner_service.symbols), len(spot_scanner_service.symbols),
    )

    yield  # ── App is running ──

    # Shutdown
    logger.info("Shutting down...")
    await futures_ws.stop()
    await spot_ws.stop()
    for task in bg_tasks:
        task.cancel()
    await asyncio.gather(*bg_tasks, return_exceptions=True)
    await collector.stop()
    logger.info("Shutdown complete.")


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="EMA Scanner API",
    description="Real-time Binance Spot + USDT Futures EMA crossover scanner.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routers ──────────────────────────────────────────────────────────────────

from app.api.scanner import router as scanner_router  # noqa: E402
app.include_router(scanner_router)


# ─── Root ─────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    return {"service": "EMA Scanner", "docs": "/docs", "status": "/api/status"}


@app.get("/health", include_in_schema=False)
async def health():
    return JSONResponse({"status": "ok", "uptime": scanner_service.uptime})
