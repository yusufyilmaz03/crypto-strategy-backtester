import numpy as np
import pandas as pd
import pytest

import data

MINUTE = 60_000


class FakeExchange:
    """Serves synthetic 1m candles from `first` up to (and including) the candle open at `last`."""

    def __init__(self, first, last, step=MINUTE):
        self.first, self.last, self.step = first, last, step
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=1000):
        self.calls.append(since)
        if since is None:
            start = max(self.first, self.last - (limit - 1) * self.step)
        else:
            start = max(since, self.first)
            start += (-start) % self.step
        rows = []
        t = start
        while t <= self.last and len(rows) < limit:
            p = 100 + (t // self.step) % 7
            rows.append([t, p, p + 1, p - 1, p + 0.5, 10.0])
            t += self.step
        return rows


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "CACHE_DIR", str(tmp_path))
    return tmp_path


def make_ohlcv(n=500, seed=0, start="2025-01-01", freq="5min"):
    """Random-walk OHLCV frame in the same shape the app uses."""
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.0005, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.002, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.002, n)))
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=n, freq=freq),
        "open": open_, "high": high, "low": low, "close": close,
        "volume": rng.uniform(1, 10, n),
    })
