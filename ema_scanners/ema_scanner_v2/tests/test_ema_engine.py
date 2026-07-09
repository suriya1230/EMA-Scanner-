"""
Full test suite for the EMA Signal Engine.

Tests cover:
  - EMA calculation (TradingView accuracy)
  - Exact crossover interpolation formula
  - BUY signal conditions
  - SELL signal conditions
  - Trend filter (EMA99 confirmation)
  - Duplicate prevention
  - Trend classification

Run with:
    pytest tests/test_ema_engine.py -v
"""

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app.services.ema_engine import EMAEngine, SignalEvent


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    return EMAEngine(short=7, mid=25, long=99)


def make_times(n: int, start_ms: int = 1_700_000_000_000) -> np.ndarray:
    """Generate n open_times spaced 1H apart (ms)."""
    return np.array([start_ms + i * 3_600_000 for i in range(n)], dtype=np.int64)


# ─── 1. EMA Calculation ───────────────────────────────────────────────────────

class TestEMACalculation:

    def test_matches_pandas_ewm_adjust_false(self, engine):
        """EMA output must be bit-identical to pandas ewm(adjust=False)."""
        np.random.seed(42)
        closes = np.random.uniform(100, 200, 500)
        ema7, ema25, ema99 = engine.calculate_emas(closes)

        s = pd.Series(closes)
        np.testing.assert_allclose(ema7,  s.ewm(span=7,  adjust=False).mean().to_numpy(), rtol=1e-10)
        np.testing.assert_allclose(ema25, s.ewm(span=25, adjust=False).mean().to_numpy(), rtol=1e-10)
        np.testing.assert_allclose(ema99, s.ewm(span=99, adjust=False).mean().to_numpy(), rtol=1e-10)

    def test_output_length_equals_input(self, engine):
        closes = np.random.uniform(100, 200, 300)
        ema7, ema25, ema99 = engine.calculate_emas(closes)
        assert len(ema7) == len(ema25) == len(ema99) == 300

    def test_single_candle(self, engine):
        closes = np.array([123.45])
        ema7, ema25, ema99 = engine.calculate_emas(closes)
        assert ema7[0] == pytest.approx(123.45)
        assert ema25[0] == pytest.approx(123.45)
        assert ema99[0] == pytest.approx(123.45)

    def test_constant_series_returns_same_value(self, engine):
        closes = np.full(200, 50.0)
        ema7, ema25, ema99 = engine.calculate_emas(closes)
        np.testing.assert_allclose(ema7,  50.0, rtol=1e-10)
        np.testing.assert_allclose(ema25, 50.0, rtol=1e-10)
        np.testing.assert_allclose(ema99, 50.0, rtol=1e-10)


# ─── 2. Interpolation Formula ─────────────────────────────────────────────────

class TestInterpolation:

    def test_exact_midpoint_cross(self, engine):
        """
        EMA7: 95→105, EMA25: 100→100
        t = (100-95)/((105-95)-(100-100)) = 5/10 = 0.5
        cross_price = 95 + 10*0.5 = 100.0
        """
        cp, ct = engine._interpolate_cross(
            ema7_prev=95.0, ema7_curr=105.0,
            ema25_prev=100.0, ema25_curr=100.0,
            candle_open_ms=1_700_000_000_000,
        )
        assert math.isclose(cp, 100.0, rel_tol=1e-9)
        expected_ms = 1_700_000_000_000 + int(0.5 * 3600 * 1000)
        assert ct == datetime.fromtimestamp(expected_ms / 1000, tz=timezone.utc)

    def test_cross_price_is_between_prev_and_curr(self, engine):
        cp, _ = engine._interpolate_cross(90.0, 110.0, 100.0, 100.0, 1_700_000_000_000)
        assert 90.0 <= cp <= 110.0

    def test_cross_time_is_within_candle(self, engine):
        start_ms = 1_700_000_000_000
        _, ct = engine._interpolate_cross(95.0, 105.0, 100.0, 100.0, start_ms)
        candle_open  = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
        candle_close = datetime.fromtimestamp((start_ms + 3_600_000) / 1000, tz=timezone.utc)
        assert candle_open <= ct <= candle_close

    def test_parallel_lines_use_midpoint(self, engine):
        """When denom ≈ 0 (parallel lines) t should default to 0.5."""
        # Both EMAs move by same amount → denom = 0
        cp, ct = engine._interpolate_cross(100.0, 110.0, 100.0, 110.0, 1_700_000_000_000)
        expected_ms = 1_700_000_000_000 + int(0.5 * 3600 * 1000)
        assert ct == datetime.fromtimestamp(expected_ms / 1000, tz=timezone.utc)

    def test_t_clamped_to_zero_when_negative(self, engine):
        """t < 0 means cross happened before candle open — clamp to 0."""
        # EMA7 was already above EMA25 at prev candle (gap widening) — t < 0
        cp, ct = engine._interpolate_cross(200.0, 210.0, 100.0, 100.0, 1_700_000_000_000)
        # t clamped to 0 → cross_price = EMA7_prev = 200
        assert math.isclose(cp, 200.0, rel_tol=1e-9)

    def test_t_clamped_to_one_when_above_one(self, engine):
        """t > 1 means cross happened after candle close — clamp to 1."""
        cp, ct = engine._interpolate_cross(100.0, 100.0, 200.0, 50.0, 1_700_000_000_000)
        expected_ms = 1_700_000_000_000 + 3_600_000
        assert ct == datetime.fromtimestamp(expected_ms / 1000, tz=timezone.utc)


# ─── 3. BUY Signal ────────────────────────────────────────────────────────────

class TestBuySignal:

    def test_buy_signal_generated_on_upward_cross_with_trend(self, engine):
        """
        EMA7 crosses above EMA25 AND EMA7 > EMA25 > EMA99 → BUY
        """
        times = make_times(5)
        # Previous candle: EMA7(98) <= EMA25(100), EMA99(110)
        # Current candle:  EMA7(102) >  EMA25(100), EMA99(99) → Bullish trend
        ema7  = np.array([96, 97, 98, 98,  102], dtype=float)
        ema25 = np.array([100,100,100,100, 100], dtype=float)
        ema99 = np.array([110,110,110,110,  99], dtype=float)

        sig = engine.detect_signal(ema7, ema25, ema99, times)
        assert sig is not None
        assert sig.signal_type == "BUY"

    def test_buy_signal_has_correct_ema_snapshot(self, engine):
        times = make_times(5)
        ema7  = np.array([96, 97, 98, 98,  102], dtype=float)
        ema25 = np.array([100,100,100,100, 100], dtype=float)
        ema99 = np.array([110,110,110,110,  99], dtype=float)

        sig = engine.detect_signal(ema7, ema25, ema99, times)
        assert sig.ema_7  == pytest.approx(102.0)
        assert sig.ema_25 == pytest.approx(100.0)
        assert sig.ema_99 == pytest.approx(99.0)

    def test_buy_signal_cross_price_not_candle_close(self, engine):
        """cross_price must NOT equal candle close — it's interpolated."""
        times = make_times(2)
        ema7  = np.array([95.0, 105.0], dtype=float)
        ema25 = np.array([100.0, 100.0], dtype=float)
        ema99 = np.array([80.0, 80.0], dtype=float)

        sig = engine.detect_signal(ema7, ema25, ema99, times)
        assert sig is not None
        # cross_price should be ~100 (interpolated), not 105 (EMA7 close)
        assert sig.cross_price != 105.0
        assert math.isclose(sig.cross_price, 100.0, rel_tol=1e-6)

    def test_no_buy_when_ema99_above_ema25(self, engine):
        """
        EMA7 crosses above EMA25 but EMA99 > EMA25 → trend not confirmed → no signal.
        """
        times = make_times(5)
        ema7  = np.array([96, 97, 98, 98,  102], dtype=float)
        ema25 = np.array([100,100,100,100, 100], dtype=float)
        ema99 = np.array([110,110,110,110, 105], dtype=float)  # EMA99 > EMA25

        sig = engine.detect_signal(ema7, ema25, ema99, times)
        assert sig is None

    def test_no_buy_when_already_above_no_cross(self, engine):
        """EMA7 was already above EMA25 — no crossover → no signal."""
        times = make_times(5)
        ema7  = np.array([105,106,107,108, 109], dtype=float)
        ema25 = np.array([100,100,100,100, 100], dtype=float)
        ema99 = np.array([90, 90, 90, 90,  90 ], dtype=float)

        sig = engine.detect_signal(ema7, ema25, ema99, times)
        assert sig is None

    def test_no_buy_with_only_two_candles_minimum(self, engine):
        """Need at least 2 candles."""
        times = make_times(1)
        ema7  = np.array([102.0])
        ema25 = np.array([100.0])
        ema99 = np.array([99.0])

        sig = engine.detect_signal(ema7, ema25, ema99, times)
        assert sig is None


# ─── 4. SELL Signal ───────────────────────────────────────────────────────────

class TestSellSignal:

    def test_sell_signal_generated_on_downward_cross_with_trend(self, engine):
        """
        EMA7 crosses below EMA25 AND EMA7 < EMA25 < EMA99 → SELL
        """
        times = make_times(5)
        # Previous: EMA7(102) >= EMA25(100)
        # Current:  EMA7(98)  <  EMA25(100), EMA99(110) → Bearish trend
        ema7  = np.array([104,103,102,102, 98 ], dtype=float)
        ema25 = np.array([100,100,100,100, 100], dtype=float)
        ema99 = np.array([90, 90, 90, 90,  110], dtype=float)

        sig = engine.detect_signal(ema7, ema25, ema99, times)
        assert sig is not None
        assert sig.signal_type == "SELL"

    def test_sell_signal_has_correct_ema_snapshot(self, engine):
        times = make_times(5)
        ema7  = np.array([104,103,102,102, 98 ], dtype=float)
        ema25 = np.array([100,100,100,100, 100], dtype=float)
        ema99 = np.array([90, 90, 90, 90,  110], dtype=float)

        sig = engine.detect_signal(ema7, ema25, ema99, times)
        assert sig.ema_7  == pytest.approx(98.0)
        assert sig.ema_25 == pytest.approx(100.0)
        assert sig.ema_99 == pytest.approx(110.0)

    def test_sell_signal_cross_price_not_candle_close(self, engine):
        times = make_times(2)
        ema7  = np.array([105.0, 95.0], dtype=float)
        ema25 = np.array([100.0, 100.0], dtype=float)
        ema99 = np.array([120.0, 120.0], dtype=float)

        sig = engine.detect_signal(ema7, ema25, ema99, times)
        assert sig is not None
        assert sig.cross_price != 95.0   # not candle close
        assert math.isclose(sig.cross_price, 100.0, rel_tol=1e-6)

    def test_no_sell_when_ema99_below_ema25(self, engine):
        """
        EMA7 crosses below EMA25 but EMA99 < EMA25 → trend not confirmed → no signal.
        """
        times = make_times(5)
        ema7  = np.array([104,103,102,102, 98 ], dtype=float)
        ema25 = np.array([100,100,100,100, 100], dtype=float)
        ema99 = np.array([90, 90, 90, 90,  90 ], dtype=float)  # EMA99 < EMA25

        sig = engine.detect_signal(ema7, ema25, ema99, times)
        assert sig is None

    def test_no_sell_when_already_below_no_cross(self, engine):
        """EMA7 was already below EMA25 — no crossover → no signal."""
        times = make_times(5)
        ema7  = np.array([95, 94, 93, 92,  91], dtype=float)
        ema25 = np.array([100,100,100,100,100], dtype=float)
        ema99 = np.array([110,110,110,110,110], dtype=float)

        sig = engine.detect_signal(ema7, ema25, ema99, times)
        assert sig is None


# ─── 5. Signal on Exactly 2 Candles ──────────────────────────────────────────

class TestMinimalCandles:

    def test_buy_with_exactly_2_candles(self, engine):
        times = make_times(2)
        ema7  = np.array([98.0, 102.0])
        ema25 = np.array([100.0, 100.0])
        ema99 = np.array([80.0, 80.0])

        sig = engine.detect_signal(ema7, ema25, ema99, times)
        assert sig is not None
        assert sig.signal_type == "BUY"

    def test_sell_with_exactly_2_candles(self, engine):
        times = make_times(2)
        ema7  = np.array([102.0, 98.0])
        ema25 = np.array([100.0, 100.0])
        ema99 = np.array([120.0, 120.0])

        sig = engine.detect_signal(ema7, ema25, ema99, times)
        assert sig is not None
        assert sig.signal_type == "SELL"


# ─── 6. Duplicate Prevention (via scanner_service, tested here conceptually) ──

class TestDuplicatePrevention:
    """
    The engine itself always returns a signal when conditions are met.
    Duplicate prevention is the scanner_service's responsibility.
    These tests verify the VALID SEQUENCE logic with manual state tracking.
    """

    def _run_sequence(self, engine, signals_sequence):
        """Simulate scanner_service duplicate filter."""
        last = None
        stored = []
        for sig in signals_sequence:
            if sig is None:
                continue
            if sig.signal_type == last:
                continue  # duplicate — skip
            stored.append(sig.signal_type)
            last = sig.signal_type
        return stored

    def test_buy_sell_buy_sell_all_stored(self, engine):
        times = make_times(2)

        buy_sig = SignalEvent("BUY",  100.0, datetime.now(tz=timezone.utc), 102.0, 100.0, 98.0)
        sell_sig = SignalEvent("SELL", 100.0, datetime.now(tz=timezone.utc), 98.0,  100.0, 102.0)

        sequence = [buy_sig, sell_sig, buy_sig, sell_sig]
        result = self._run_sequence(engine, sequence)
        assert result == ["BUY", "SELL", "BUY", "SELL"]

    def test_duplicate_buy_filtered(self, engine):
        buy_sig = SignalEvent("BUY",  100.0, datetime.now(tz=timezone.utc), 102.0, 100.0, 98.0)
        sequence = [buy_sig, buy_sig, buy_sig]
        result = self._run_sequence(engine, sequence)
        assert result == ["BUY"]   # only first stored

    def test_duplicate_sell_filtered(self, engine):
        sell_sig = SignalEvent("SELL", 100.0, datetime.now(tz=timezone.utc), 98.0, 100.0, 102.0)
        sequence = [sell_sig, sell_sig]
        result = self._run_sequence(engine, sequence)
        assert result == ["SELL"]  # only first stored


# ─── 7. Trend Classification ──────────────────────────────────────────────────

class TestTrendClassification:

    def test_bullish(self, engine):
        assert engine.classify_trend(110, 100, 90) == "Bullish"

    def test_bearish(self, engine):
        assert engine.classify_trend(90, 100, 110) == "Bearish"

    def test_neutral_mixed(self, engine):
        assert engine.classify_trend(100, 110, 90) == "Neutral"

    def test_neutral_all_equal(self, engine):
        assert engine.classify_trend(100, 100, 100) == "Neutral"

    def test_neutral_7_between_25_and_99(self, engine):
        assert engine.classify_trend(105, 100, 110) == "Neutral"


# ─── 8. EMA Accuracy with 3000 Candles ───────────────────────────────────────

class TestEMAwith3000Candles:

    def test_uptrend_ema_order(self, engine):
        """On a rising price series, EMA7 > EMA25 > EMA99 at the end."""
        closes = np.linspace(50, 120, 3000)
        ema7, ema25, ema99 = engine.calculate_emas(closes)
        assert ema7[-1] > ema25[-1] > ema99[-1]

    def test_downtrend_ema_order(self, engine):
        """On a falling price series, EMA7 < EMA25 < EMA99 at the end."""
        closes = np.linspace(120, 50, 3000)
        ema7, ema25, ema99 = engine.calculate_emas(closes)
        assert ema7[-1] < ema25[-1] < ema99[-1]

    def test_ema_warmup_accuracy(self, engine):
        """With 3000 candles the EMA should be well converged vs pandas."""
        np.random.seed(7)
        closes = np.random.uniform(100, 200, 3000)
        ema7, ema25, ema99 = engine.calculate_emas(closes)
        s = pd.Series(closes)
        np.testing.assert_allclose(ema7[-1],  s.ewm(span=7,  adjust=False).mean().iloc[-1], rtol=1e-10)
        np.testing.assert_allclose(ema25[-1], s.ewm(span=25, adjust=False).mean().iloc[-1], rtol=1e-10)
        np.testing.assert_allclose(ema99[-1], s.ewm(span=99, adjust=False).mean().iloc[-1], rtol=1e-10)
