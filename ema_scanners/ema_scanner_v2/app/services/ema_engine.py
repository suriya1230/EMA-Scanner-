"""
EMA Engine — TradingView-identical EMA calculation + two-step signal detection.

Signal Logic:
═════════════════════════════════════════════════════════════════════════════

BUY SIGNAL:
  Step 1: EMA7 crosses above EMA25
          Previous candle: EMA7 <= EMA25
          Current  candle: EMA7 >  EMA25

  Step 2: EMA7 crosses above EMA99
          Previous candle: EMA7 <= EMA99
          Current  candle: EMA7 >  EMA99
          (must happen AFTER Step 1)

  Signal fires on the candle where Step 2 completes, IF:
          Final alignment: EMA7 > EMA25 > EMA99

  Entry Time  = exact interpolated EMA7/EMA99 crossover timestamp (NOT candle open)
  Entry Price = exact interpolated EMA7/EMA99 crossover price     (NOT candle open)

SELL SIGNAL:
  Step 1: EMA7 crosses below EMA25
          Previous candle: EMA7 >= EMA25
          Current  candle: EMA7 <  EMA25

  Step 2: EMA7 crosses below EMA99
          Previous candle: EMA7 >= EMA99
          Current  candle: EMA7 <  EMA99
          (must happen AFTER Step 1)

  Signal fires on the candle where Step 2 completes, IF:
          Final alignment: EMA99 > EMA25 > EMA7

  Entry Time  = exact interpolated EMA7/EMA99 crossover timestamp (NOT candle open)
  Entry Price = exact interpolated EMA7/EMA99 crossover price     (NOT candle open)

Duplicate Prevention:
  - No two consecutive BUY signals (must alternate BUY→SELL→BUY)
  - No two consecutive SELL signals (must alternate SELL→BUY→SELL)

State Reset:
  - BUY Step 1 resets any in-progress SELL setup (and vice versa)
  - After signal fires, that direction's setup resets

Interpolation (EMA7 / EMA99 intersection):
  t           = (EMA99_prev - EMA7_prev) / ((EMA7_curr - EMA7_prev) - (EMA99_curr - EMA99_prev))
  cross_price = EMA7_prev  + (EMA7_curr  - EMA7_prev)  * t
  cross_time  = candle_open_ms + t * 3_600_000
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.core.config import settings

logger = logging.getLogger(__name__)

CANDLE_SECONDS   = 3600   # 1H in seconds — default candle span, overridable via candle_ms params below
LOOKBACK_CANDLES = 720    # 30 days × 24 hours — default lookback, for the 1H interval specifically

# Candle span in ms for every supported backtest interval — used to interpolate
# crossover timing and to size the "last 30 days" lookback window correctly
# for timeframes other than 1H.
INTERVAL_MS: dict[str, int] = {
    "1m":  60_000,
    "15m": 900_000,
    "1h":  3_600_000,
    "2h":  7_200_000,
    "4h":  14_400_000,
    "6h":  21_600_000,
}


def lookback_for_interval(interval: str, days: int = 30) -> int:
    """Number of candles of `interval` spanning the last `days` days."""
    return int(days * 24 * 3_600_000 / INTERVAL_MS[interval])


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class SignalEvent:
    """
    A validated BUY or SELL signal.
    entry_price / entry_time = exact interpolated EMA7/EMA99 intersection.
    cross_price / cross_time = same value (kept for DB compatibility).
    """
    signal_type:  str       # "BUY" | "SELL"
    cross_price:  float     # exact EMA7/EMA99 intersection price (= entry price)
    cross_time:   datetime  # exact EMA7/EMA99 intersection time  (= entry time)
    ema_7:  float
    ema_25: float
    ema_99: float


# ─── Engine ───────────────────────────────────────────────────────────────────

class EMAEngine:
    """
    Stateless EMA engine.
    Feed it sorted close prices + open_times_ms (oldest first).
    """

    def __init__(
        self,
        short: int = settings.EMA_SHORT,   # 7
        mid:   int = settings.EMA_MID,     # 25
        long:  int = settings.EMA_LONG,    # 99
    ):
        self.short = short
        self.mid   = mid
        self.long  = long

    # ── EMA Calculation ───────────────────────────────────────────────────────

    def calculate_emas(
        self, closes: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        TradingView-identical EMA using pandas ewm(adjust=False).
        Input must be sorted oldest → newest.
        """
        s = pd.Series(closes, dtype=float)
        ema7  = s.ewm(span=self.short, adjust=False).mean().to_numpy()
        ema25 = s.ewm(span=self.mid,   adjust=False).mean().to_numpy()
        ema99 = s.ewm(span=self.long,  adjust=False).mean().to_numpy()
        return ema7, ema25, ema99

    # ── Exact EMA7/EMA99 Crossover Interpolation ──────────────────────────────

    @staticmethod
    def _interpolate_ema7_ema99(
        e7_prev:  float,
        e7_curr:  float,
        e99_prev: float,
        e99_curr: float,
        candle_open_ms: int,
        candle_ms: int = CANDLE_SECONDS * 1000,
    ) -> tuple[float, datetime]:
        """
        Linearly interpolate the exact EMA7/EMA99 crossover point.

        Formula:
          t           = (EMA99_prev - EMA7_prev) / ((EMA7_curr - EMA7_prev) - (EMA99_curr - EMA99_prev))
          cross_price = EMA7_prev + (EMA7_curr - EMA7_prev) * t
          cross_time  = candle_open_ms + t * candle_ms

        t is clamped to [0, 1] — stays inside the candle.
        `candle_ms` defaults to 1H (3,600,000ms); pass INTERVAL_MS[interval] for
        other timeframes so the interpolated time stays inside the correct span.
        """
        denom = (e7_curr - e7_prev) - (e99_curr - e99_prev)

        if abs(denom) < 1e-12:
            t = 0.5
        else:
            t = (e99_prev - e7_prev) / denom

        t = max(0.0, min(1.0, t))

        cross_price = e7_prev + (e7_curr - e7_prev) * t
        cross_ms    = int(candle_open_ms + t * candle_ms)
        cross_time  = datetime.fromtimestamp(cross_ms / 1000, tz=timezone.utc)

        return cross_price, cross_time

    # ── Signal Detection ──────────────────────────────────────────────────────

    def detect_signals(
        self,
        ema7:          np.ndarray,
        ema25:         np.ndarray,
        ema99:         np.ndarray,
        open_times_ms: np.ndarray,
        lookback:      int = LOOKBACK_CANDLES,
        candle_ms:     int = CANDLE_SECONDS * 1000,
    ) -> list[SignalEvent]:
        """
        2-Candle Crossover Signal Detection.

        BUY  — valid signal:
          Candle N  : EMA7 crosses ABOVE EMA25  (e7_prev <= e25_prev AND e7_curr > e25_curr)
          Candle N or N+1: EMA7 crosses ABOVE EMA99  (e7_prev <= e99_prev AND e7_curr > e99_curr)
          Both crossovers must happen within 2 candles.
          If EMA7/EMA99 cross does NOT happen by candle N+1 → FAKE, ignored.
          Entry = exact interpolated EMA7/EMA99 crossover price+time.

        SELL — valid signal:
          Candle N  : EMA7 crosses BELOW EMA25  (e7_prev >= e25_prev AND e7_curr < e25_curr)
          Candle N or N+1: EMA7 crosses BELOW EMA99  (e7_prev >= e99_prev AND e7_curr < e99_curr)
          Both crossovers must happen within 2 candles.
          If EMA7/EMA99 cross does NOT happen by candle N+1 → FAKE, ignored.
          Entry = exact interpolated EMA7/EMA99 crossover price+time.

        Strict BUY→SELL→BUY alternation — no consecutive duplicates.
        """
        n = len(ema7)
        if n < 2:
            return []

        signals: list[SignalEvent] = []
        last_signal: str | None = None

        # Track pending EMA7/EMA25 cross — stores candle index where cross happened.
        # -1 means no pending cross. Expires if EMA7/EMA99 cross doesn't happen
        # on the same candle (N) or the very next candle (N+1).
        pending_buy_cross_idx:  int = -1   # candle where EMA7 crossed above EMA25
        pending_sell_cross_idx: int = -1   # candle where EMA7 crossed below EMA25

        scan_start = max(1, n - lookback)

        for i in range(1, n):
            e7p  = ema7[i - 1];  e7c  = ema7[i]
            e25p = ema25[i - 1]; e25c = ema25[i]
            e99p = ema99[i - 1]; e99c = ema99[i]

            # ── Expire pending crosses older than 1 candle ────────────────
            # Cross on candle N is valid only on N or N+1.
            # If we reach N+2 without the EMA99 cross → fake, discard.
            if pending_buy_cross_idx  != -1 and i > pending_buy_cross_idx  + 1:
                pending_buy_cross_idx  = -1
                logger.debug("candle[%d] BUY cross expired (no EMA99 cross within 2 candles)", i)
            if pending_sell_cross_idx != -1 and i > pending_sell_cross_idx + 1:
                pending_sell_cross_idx = -1
                logger.debug("candle[%d] SELL cross expired (no EMA99 cross within 2 candles)", i)

            # ── Detect EMA7 crossing above EMA25 — BUY Step 1 ───────────
            # PRE-CONDITION: on the PREVIOUS candle, alignment MUST be
            # EMA99 > EMA25 > EMA7 (full bearish stack — EMA7 at bottom).
            # This guarantees the crossover starts from the correct position.
            if e7p <= e25p and e7c > e25c:
                if e99p > e25p > e7p and e25c > e99c:
                    # Previous candle: bearish stack ✅
                    # Current candle: EMA25 still above EMA99 (EMA99 not crossed yet) ✅
                    pending_buy_cross_idx  = i
                    pending_sell_cross_idx = -1
                    logger.debug("candle[%d] BUY step1 VALID: EMA99>EMA25>EMA7 pre-alignment confirmed", i)
                else:
                    logger.debug(
                        "candle[%d] BUY step1 REJECTED: pre-alignment not EMA99>EMA25>EMA7 "
                        "(prev EMA7=%.6f EMA25=%.6f EMA99=%.6f)",
                        i, e7p, e25p, e99p,
                    )

            # ── Detect EMA7 crossing below EMA25 — SELL Step 1 ──────────
            # PRE-CONDITION: on the PREVIOUS candle, alignment MUST be
            # EMA7 > EMA25 > EMA99 (full bullish stack — EMA7 at top).
            # This guarantees the crossover starts from the correct position.
            if e7p >= e25p and e7c < e25c:
                if e7p > e25p > e99p and e25c < e99c:
                    # Previous candle: bullish stack ✅
                    # Current candle: EMA25 still below EMA99 (EMA99 not crossed yet) ✅
                    pending_sell_cross_idx = i
                    pending_buy_cross_idx  = -1
                    logger.debug("candle[%d] SELL step1 VALID: EMA7>EMA25>EMA99 pre-alignment confirmed", i)
                else:
                    logger.debug(
                        "candle[%d] SELL step1 REJECTED: pre-alignment not EMA7>EMA25>EMA99 "
                        "(prev EMA7=%.6f EMA25=%.6f EMA99=%.6f)",
                        i, e7p, e25p, e99p,
                    )

            # ── BUY signal: EMA7 crosses above EMA99 within 2 candles of EMA25 cross
            if (pending_buy_cross_idx != -1
                    and e7p <= e99p and e7c > e99c
                    and last_signal != "BUY"):
                # Minimum separation check — all 3 EMAs must be meaningfully spread.
                # EMA7 vs EMA99 must be at least 0.05% apart to confirm real trend.
                # Rejects signals where all EMAs are bunched together (sideways noise).
                min_sep = e99c * 0.0005   # 0.05% of EMA99 price
                if e7c > e25c > e99c and (e7c - e99c) >= min_sep:
                    cross_price, cross_time = self._interpolate_ema7_ema99(
                        e7p, e7c, e99p, e99c, int(open_times_ms[i]), candle_ms
                    )
                    if i >= scan_start:
                        signals.append(SignalEvent(
                            signal_type="BUY",
                            cross_price=cross_price,
                            cross_time=cross_time,
                            ema_7=float(e7c),
                            ema_25=float(e25c),
                            ema_99=float(e99c),
                        ))
                        logger.info(
                            "BUY candle[%d] entry_price=%.6f entry_time=%s "
                            "EMA7=%.4f EMA25=%.4f EMA99=%.4f",
                            i, cross_price, cross_time.isoformat(),
                            e7c, e25c, e99c,
                        )
                    last_signal           = "BUY"
                else:
                    logger.debug(
                        "candle[%d] BUY rejected — alignment or separation not met "
                        "(EMA7=%.6f EMA25=%.6f EMA99=%.6f spread=%.6f min=%.6f)",
                        i, e7c, e25c, e99c, e7c - e99c, min_sep,
                    )
                pending_buy_cross_idx = -1
                continue

            # ── SELL signal: EMA7 crosses below EMA99 within 2 candles of EMA25 cross
            if (pending_sell_cross_idx != -1
                    and e7p >= e99p and e7c < e99c
                    and last_signal != "SELL"):
                min_sep = e7c * 0.0005   # 0.05% of EMA7 price
                if e99c > e25c > e7c and (e99c - e7c) >= min_sep:
                    cross_price, cross_time = self._interpolate_ema7_ema99(
                        e7p, e7c, e99p, e99c, int(open_times_ms[i]), candle_ms
                    )
                    if i >= scan_start:
                        signals.append(SignalEvent(
                            signal_type="SELL",
                            cross_price=cross_price,
                            cross_time=cross_time,
                            ema_7=float(e7c),
                            ema_25=float(e25c),
                            ema_99=float(e99c),
                        ))
                        logger.info(
                            "SELL candle[%d] entry_price=%.6f entry_time=%s "
                            "EMA7=%.4f EMA25=%.4f EMA99=%.4f",
                            i, cross_price, cross_time.isoformat(),
                            e7c, e25c, e99c,
                        )
                    last_signal            = "SELL"
                else:
                    logger.debug(
                        "candle[%d] SELL rejected — alignment or separation not met "
                        "(EMA7=%.6f EMA25=%.6f EMA99=%.6f spread=%.6f min=%.6f)",
                        i, e7c, e25c, e99c, e99c - e7c, min_sep,
                    )
                pending_sell_cross_idx = -1

        return signals

    # ── Live WebSocket detection (single new candle) ──────────────────────────

    def detect_signal(
        self,
        ema7:          np.ndarray,
        ema25:         np.ndarray,
        ema99:         np.ndarray,
        open_times_ms: np.ndarray,
        candle_ms:     int = CANDLE_SECONDS * 1000,
    ) -> SignalEvent | None:
        """
        Called on every closed candle by the WebSocket handler (1H) or the
        periodic REST refresh loop (2H/4H/6H).
        Runs full detect_signals() with lookback=2 (only last 2 candles can
        emit a new signal — the 2-candle crossover window).
        Returns the signal if one fired on the last candle, else None.
        `candle_ms` must match the candle interval being processed — pass
        INTERVAL_MS[interval] for anything other than 1H.
        """
        if len(ema7) < 2:
            return None

        # Use lookback=2 — only the last 2 candles can produce a new live signal.
        # Full history is already in ema7/ema25/ema99 arrays for accurate EMA values.
        all_signals = self.detect_signals(
            ema7, ema25, ema99, open_times_ms, lookback=2, candle_ms=candle_ms
        )
        if not all_signals:
            return None

        # Signal must have fired on the last candle (open_time matches last candle)
        last_open_ms = int(open_times_ms[-1])
        last_open_dt = datetime.fromtimestamp(last_open_ms / 1000, tz=timezone.utc)

        sig = all_signals[-1]
        sig_candle_open_ms = int(sig.cross_time.timestamp() * 1000)

        # cross_time is inside the last candle's window
        if last_open_ms <= sig_candle_open_ms < last_open_ms + candle_ms:
            return sig

        # Also accept if cross_time is exactly at candle boundary (float precision)
        if abs(sig_candle_open_ms - last_open_ms) < 1000:
            return sig

        return None

    # ── Trend Classification ───────────────────────────────────────────────────

    @staticmethod
    def classify_trend(ema7: float, ema25: float, ema99: float) -> str:
        if ema7 > ema25 > ema99:
            return "Bullish"
        if ema7 < ema25 < ema99:
            return "Bearish"
        return "Neutral"