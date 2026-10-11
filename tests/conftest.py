import numpy as np
import pandas as pd
import pytest

from kripto_bot.core import data

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


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """Every file the app would write lands in tmp_path, never in runtime/ or results/."""
    from kripto_bot import config, instances, settings
    from kripto_bot.analysis import multi_backtest, research
    from kripto_bot.app import dashboard, playground, reporting, run_all
    from kripto_bot.trading import store

    rt = tmp_path / "runtime"
    rt.mkdir()
    for mod, attr, name in [(store, "DB_FILE", "paper.db"), (instances, "INSTANCES_FILE", "instances.json"),
                            (settings, "SETTINGS_FILE", "settings.json"), (playground, "PLAYGROUND_DB", "playground.db"),
                            (reporting, "STATUS_FILE", "status.json"), (run_all, "STATUS_FILE", "status.json"),
                            (reporting, "LOG_DIR", "logs"), (run_all, "LOG_DIR", "logs"),
                            (config, "KILL_SWITCH_FILE", "KILL_SWITCH")]:
        monkeypatch.setattr(mod, attr, str(rt / name))
    monkeypatch.setattr(multi_backtest, "OUT_FILE", str(tmp_path / "multi_backtest_strategies.csv"))
    monkeypatch.setattr(dashboard, "BACKTEST_FILE", str(tmp_path / "multi_backtest_strategies.csv"))
    for attr in ("RESULTS_FILE", "FOLDS_FILE", "SUMMARY_FILE", "META_FILE", "HOLDOUT_FILE"):
        monkeypatch.setattr(research, attr, str(tmp_path / getattr(research, attr).rsplit("/", 1)[-1]))
    monkeypatch.setattr(data, "CACHE_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("kripto_bot.paths.runtime", lambda name: name if name.startswith("/") else str(rt / name))
