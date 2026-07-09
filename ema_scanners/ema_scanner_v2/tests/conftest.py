import pytest
import numpy as np
from app.services.ema_engine import EMAEngine


@pytest.fixture(scope="session")
def engine():
    return EMAEngine(short=7, mid=25, long=99, window=3)


@pytest.fixture
def sample_closes():
    """500 realistic price closes."""
    np.random.seed(0)
    prices = [100.0]
    for _ in range(499):
        prices.append(prices[-1] * (1 + np.random.normal(0, 0.002)))
    return np.array(prices)


@pytest.fixture
def sample_open_times():
    start = 1_700_000_000_000
    return np.array([start + i * 3_600_000 for i in range(500)], dtype=np.int64)
