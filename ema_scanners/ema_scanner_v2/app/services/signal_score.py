"""
Signal Score (0-100) — computed ONCE at signal-detection time and frozen
onto that Signal row forever (see `score` column on the Signal model).
Never recomputed later against live market data, so the same historical
signal always shows the same score no matter when you look at it.

Built ENTIRELY from stored candle data (OHLCV up to and including the
signal's own candle) — no live ticker snapshot — so this function produces
the identical result whether called the moment a signal fires (live
WebSocket path) or reconstructed afterward from historical candles
(backfill path). That reproducibility is what makes "frozen at detection
time" actually meaningful.

Grades the signal's direction (BUY/SELL), not the coin's current live
trend — weighted blend of six factors:
  20% EMA separation   — |EMA7-EMA99| as % of price, at the signal candle
  20% Higher-TF agree  — did the 4H/6H trend (as of the signal's own time,
                          never using candles from after it) agree
  15% Momentum         — 1H/24H price change (from candles) agreeing with direction
  15% Volatility (ATR) — EMA separation relative to ATR14, at the signal candle
  15% Volume           — rolling 24-candle volume sum as a liquidity proxy
  15% Distance/EMA99   — how extended price is from EMA99, at the signal candle
"""
from __future__ import annotations

import numpy as np

from app.core.config import settings
from app.services.ema_engine import EMAEngine

HIGHER_TF_INTERVALS = ("4h", "6h")


def compute_score(
    closes: np.ndarray, highs: np.ndarray, lows: np.ndarray, volumes: np.ndarray,
    ema7: np.ndarray, ema25: np.ndarray, ema99: np.ndarray,
    idx: int, signal_type: str, higher_tf_agree: int, engine: EMAEngine,
) -> float:
    """All arrays are the full oldest->newest candle history; `idx` is the
    index of the signal's own candle. Every factor uses ONLY data up to and
    including `idx` — never anything after, which would leak the future
    into what's supposed to be a frozen, point-in-time score."""
    price = float(closes[idx])
    if price <= 0:
        return 0.0
    direction = 1 if signal_type == "BUY" else -1

    ema_sep_pct = abs(ema7[idx] - ema99[idx]) / price * 100
    ema_sep_score = min(100.0, (ema_sep_pct / 3.0) * 100)

    atr = engine.compute_atr(highs[:idx + 1], lows[:idx + 1], closes[:idx + 1])
    atr_pct = (atr / price * 100) if price > 0 else 0.0
    volatility_score = 50.0 if atr_pct <= 0 else min(100.0, (ema_sep_pct / atr_pct) * 50)

    change_1h = ((closes[idx] - closes[idx - 1]) / closes[idx - 1] * 100) if idx >= 1 and closes[idx - 1] else 0.0
    change_24h = ((closes[idx] - closes[idx - 24]) / closes[idx - 24] * 100) if idx >= 24 and closes[idx - 24] else change_1h
    avg_change = (change_1h + change_24h) / 2
    momentum_score = max(0.0, min(100.0, 50 + (avg_change * direction) * 10))

    higher_tf_score = higher_tf_agree * 50.0

    window = volumes[max(0, idx - 23):idx + 1]
    vol_24h = float(window.sum())
    if vol_24h <= settings.MIN_VOLUME_USDT_SIGNAL:
        volume_score = 0.0
    else:
        volume_score = min(100.0, (vol_24h - settings.MIN_VOLUME_USDT_SIGNAL)
                            / (200_000_000 - settings.MIN_VOLUME_USDT_SIGNAL) * 100)

    ema99v = float(ema99[idx])
    dist_pct = abs(price - ema99v) / ema99v * 100 if ema99v > 0 else 0.0
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


async def higher_tf_agreement(
    symbol: str, market: str, cross_time_ms: int, direction: int, engine: EMAEngine,
) -> int:
    """How many of the 4H/6H trends, reconstructed using ONLY candles that
    existed at/before `cross_time_ms`, agree with the signal's direction (0-2).
    The point-in-time filter is what keeps a historical score honest — it
    must never see 4H/6H candles from after the signal happened."""
    from app.db.database import AsyncSessionLocal
    from app.services.repository import CandleRepository

    agree = 0
    for interval in HIGHER_TF_INTERVALS:
        async with AsyncSessionLocal() as session:
            candles = await CandleRepository.get_candles(
                session, symbol, interval=interval, market=market, limit=settings.CANDLES_LIMIT
            )
        candles = [c for c in candles if c.open_time <= cross_time_ms]
        if len(candles) < 2:
            continue
        closes = np.array([c.close for c in candles], dtype=float)
        ema7, ema25, ema99 = engine.calculate_emas(closes)
        tf_trend = engine.classify_trend(float(ema7[-1]), float(ema25[-1]), float(ema99[-1]))
        if (direction == 1 and tf_trend == "Bullish") or (direction == -1 and tf_trend == "Bearish"):
            agree += 1
    return agree
