"""
Binance WebSocket Manager — real-time 1H candle push for the scanner table.

This restores live push-based updates on top of the market-data redesign:
the continuous REST collector (market_data_collector.py) keeps ALL
timeframes eventually-consistent and is the self-healing fallback, but the
scanner table's 1H view needs sub-second freshness, which only a WebSocket
push can give — a REST batch cycle over 100+ symbols simply can't match that
latency. So this covers the same "real-time" role the original 1H WS
implementation had, now parameterized per-market.

Subscribes to <symbol>@kline_1h combined streams. Binance allows up to 200
streams per combined-stream connection, so symbols are batched.

Stream URL format (same shape for both spot and futures):
    wss://<host>/stream?streams=btcusdt@kline_1h/ethusdt@kline_1h/...

Every message is routed to ScannerService.process_live_candle(), which
writes to Postgres FIRST and only recomputes EMAs by reading back from
Postgres — never directly from the WS payload. See scanner_service.py.
"""

from __future__ import annotations

import asyncio
import json
import logging

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

logger = logging.getLogger(__name__)

MAX_STREAMS_PER_CONN = 200
RECONNECT_DELAY = 5  # seconds


class BinanceWSManager:
    """
    Manages one or more WebSocket connections to a Binance combined-stream
    endpoint for a single market (spot or futures), routing kline events to
    that market's ScannerService.
    """

    def __init__(self, scanner, ws_base: str, market: str):
        self._scanner = scanner
        self._ws_base = ws_base
        self._market = market
        self._tasks: list[asyncio.Task] = []

    async def start(self):
        """Start all WebSocket listener tasks."""
        symbols = self._scanner.symbols
        if not symbols:
            logger.warning("[%s] No symbols to stream.", self._market)
            return

        batches = [
            symbols[i : i + MAX_STREAMS_PER_CONN]
            for i in range(0, len(symbols), MAX_STREAMS_PER_CONN)
        ]

        for batch_idx, batch in enumerate(batches):
            task = asyncio.create_task(
                self._listen_batch(batch, batch_idx),
                name=f"ws_{self._market}_batch_{batch_idx}",
            )
            self._tasks.append(task)

        logger.info(
            "[%s] Started %d WebSocket connection(s) for %d symbols",
            self._market, len(batches), len(symbols),
        )

    async def stop(self):
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _listen_batch(self, symbols: list[str], batch_idx: int):
        """Maintain a persistent WebSocket connection for a batch of symbols.
        Auto-reconnects on failure — never lets one bad batch kill the app."""
        stream_names = "/".join(f"{s.lower()}@kline_1h" for s in symbols)
        url = f"{self._ws_base}/stream?streams={stream_names}"

        while True:
            try:
                logger.info(
                    "[%s] Connecting WS batch %d (%d symbols)...",
                    self._market, batch_idx, len(symbols),
                )
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                    max_size=2**23,  # 8 MB
                ) as ws:
                    logger.info("[%s] WS batch %d connected.", self._market, batch_idx)
                    async for raw_msg in ws:
                        await self._handle_message(raw_msg)

            except asyncio.CancelledError:
                logger.info("[%s] WS batch %d cancelled.", self._market, batch_idx)
                return
            except (ConnectionClosed, WebSocketException) as e:
                logger.warning(
                    "[%s] WS batch %d disconnected: %s. Reconnecting in %ds...",
                    self._market, batch_idx, e, RECONNECT_DELAY,
                )
            except Exception as e:
                logger.error(
                    "[%s] WS batch %d unexpected error: %s. Reconnecting in %ds...",
                    self._market, batch_idx, e, RECONNECT_DELAY,
                )

            await asyncio.sleep(RECONNECT_DELAY)

    async def _handle_message(self, raw_msg: str | bytes):
        """Parse a combined stream message and route to ScannerService."""
        try:
            msg = json.loads(raw_msg)
        except json.JSONDecodeError:
            logger.debug("[%s] Non-JSON WS message: %s", self._market, raw_msg[:100])
            return

        # Combined stream format: {"stream": "btcusdt@kline_1h", "data": {...}}
        data = msg.get("data", msg)
        if data.get("e") != "kline":
            return

        kline = data["k"]
        symbol = kline["s"].upper()
        is_closed = kline["x"]  # True when candle is closed

        candle_data = {
            "open_time": int(kline["t"]),
            "open": float(kline["o"]),
            "high": float(kline["h"]),
            "low": float(kline["l"]),
            "close": float(kline["c"]),
            "volume": float(kline["v"]),
            "close_time": int(kline["T"]),
        }

        await self._scanner.process_live_candle(symbol, candle_data, is_closed)
