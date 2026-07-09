"""
Extends the futures scan universe beyond Binance by pulling in USDT-margined
perpetuals from Bybit and OKX for base assets Binance doesn't already list.

This module only builds the additional-symbol list (raw id, base asset,
exchange, market, 24h USDT volume) via each exchange's public ticker
endpoint — no API key required, and no candle/EMA data is touched here.

Scan order is Binance -> Bybit -> OKX; each base asset is attributed to the
first source that has it, and volumes are never merged/summed across
exchanges.
"""

from __future__ import annotations

import logging
from typing import Iterable

import aiohttp

from app.core.config import settings

logger = logging.getLogger(__name__)

STABLECOIN_BASES = {"USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EUR", "EURI", "USD1"}

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)


async def fetch_bybit_perp_universe(
    session: aiohttp.ClientSession, excluded_bases: set[str]
) -> list[dict]:
    """USDT-margined linear perpetuals on Bybit.

    `category=linear` also contains USDC-margined perpetuals (symbol like
    "BTCPERP", no USDT suffix) and dated/delivery contracts (symbol like
    "BTC-27MAR26") — both are filtered out, leaving only plain "XXXUSDT"
    perpetuals.
    """
    url = f"{settings.BYBIT_REST}/v5/market/tickers"
    try:
        async with session.get(url, params={"category": "linear"}, timeout=_REQUEST_TIMEOUT) as resp:
            resp.raise_for_status()
            payload = await resp.json()
    except Exception as exc:
        logger.warning("Bybit universe fetch failed (skipping Bybit): %s", exc)
        return []

    if payload.get("retCode") != 0:
        logger.warning("Bybit universe fetch returned an error: %s", payload.get("retMsg"))
        return []

    out: list[dict] = []
    seen_bases: set[str] = set()
    for item in payload.get("result", {}).get("list", []):
        symbol = item.get("symbol", "")
        if not symbol.endswith("USDT") or "-" in symbol:
            continue  # USDC-margined perp or a dated/delivery contract
        base = symbol[: -len("USDT")]
        if not base or base in STABLECOIN_BASES:
            continue
        if base in excluded_bases or base in seen_bases:
            continue
        try:
            turnover = float(item.get("turnover24h") or 0)
        except (TypeError, ValueError):
            continue
        if turnover < settings.MIN_VOLUME_USDT_COLLECT:
            continue
        seen_bases.add(base)
        try:
            price = float(item.get("lastPrice") or 0)
            change_pct = float(item.get("price24hPcnt") or 0) * 100
        except (TypeError, ValueError):
            price, change_pct = 0.0, 0.0
        out.append({
            "symbol": symbol,
            "base": base,
            "exchange": "bybit",
            "market": "futures",
            "volume": turnover,
            "price": price,
            "change_24h": change_pct,
        })
    return out


async def fetch_okx_perp_universe(
    session: aiohttp.ClientSession, excluded_bases: set[str]
) -> list[dict]:
    """USDT-margined perpetual swaps on OKX ("<BASE>-USDT-SWAP" instruments).

    `instType=SWAP` is perpetuals-only (OKX puts dated contracts under a
    separate FUTURES instType, so no date filtering is needed here), but it
    also contains USDC-margined ("<BASE>-USDC-SWAP") and inverse/coin-margined
    ("<BASE>-USD-SWAP") instruments, which are excluded.
    """
    url = f"{settings.OKX_REST}/api/v5/market/tickers"
    try:
        async with session.get(url, params={"instType": "SWAP"}, timeout=_REQUEST_TIMEOUT) as resp:
            resp.raise_for_status()
            payload = await resp.json()
    except Exception as exc:
        logger.warning("OKX universe fetch failed (skipping OKX): %s", exc)
        return []

    if payload.get("code") != "0":
        logger.warning("OKX universe fetch returned an error: %s", payload.get("msg"))
        return []

    out: list[dict] = []
    seen_bases: set[str] = set()
    for item in payload.get("data", []):
        inst_id = item.get("instId", "")  # e.g. "BTC-USDT-SWAP"
        parts = inst_id.split("-")
        if len(parts) != 3 or parts[1] != "USDT" or parts[2] != "SWAP":
            continue  # USDC-margined, inverse/coin-margined, or malformed id
        base = parts[0]
        if not base or base in STABLECOIN_BASES:
            continue
        if base in excluded_bases or base in seen_bases:
            continue
        try:
            vol_ccy_24h = float(item.get("volCcy24h") or 0)
            last = float(item.get("last") or 0)
        except (TypeError, ValueError):
            continue
        volume = vol_ccy_24h * last
        if volume < settings.MIN_VOLUME_USDT_COLLECT:
            continue
        seen_bases.add(base)
        try:
            open_24h = float(item.get("open24h") or 0)
            change_pct = ((last - open_24h) / open_24h * 100) if open_24h else 0.0
        except (TypeError, ValueError):
            change_pct = 0.0
        out.append({
            "symbol": inst_id,
            "base": base,
            "exchange": "okx",
            "market": "futures",
            "volume": volume,
            "price": last,
            "change_24h": change_pct,
        })
    return out


async def build_extended_futures_universe(
    session: aiohttp.ClientSession, binance_bases: Iterable[str]
) -> list[dict]:
    """
    Combined Bybit + OKX additions to the futures universe, for base assets
    not already covered by `binance_bases`. Each base appears exactly once,
    attributed to whichever of Bybit/OKX had it first — Bybit is queried
    first per the required scan order (Binance -> Bybit -> OKX).

    If one exchange's request fails it's logged and skipped; the other
    exchange's results (and the Binance universe the caller already has)
    are unaffected.
    """
    excluded = {b.upper() for b in binance_bases}

    bybit_additions = await fetch_bybit_perp_universe(session, excluded)
    excluded = excluded | {item["base"] for item in bybit_additions}

    okx_additions = await fetch_okx_perp_universe(session, excluded)

    return bybit_additions + okx_additions
