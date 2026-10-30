import numpy as np
import pandas as pd
import pytest

from kripto_bot.analysis import exits, lower_tf
from kripto_bot.analysis import survivorship as sv

START = pd.Timestamp("2024-01-01")
DAYS = 260


def _hours(seed):
    rng = np.random.default_rng(seed)
    n = DAYS * 24
    close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.012, n)))
    open_ = np.r_[100, close[:-1]]
    return pd.DataFrame({"timestamp": pd.date_range(START, periods=n, freq="1h"), "open": open_,
                         "high": np.maximum(open_, close) * 1.002, "low": np.minimum(open_, close) * 0.998,
                         "close": close, "volume": 1.0, "quote_volume": rng.uniform(1e6, 2e6, n)})


def _agg(h, rule):
    g = h.set_index("timestamp").resample(rule)
    return pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(), "close": g.close.last(),
                         "volume": g.volume.sum(), "quote_volume": g.quote_volume.sum()}).reset_index()


HOURS = {f"C{k}USDT": _hours(k) for k in range(3)}
LOWER = {(s, "1h"): h for s, h in HOURS.items()} | {(s, "4h"): _agg(h, "4h") for s, h in HOURS.items()}


def fake_load(symbol, tf, refresh=False):
    return LOWER[(symbol, tf)]


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(sv, "PERIODS", {"B": ("2024-04-01", "2024-06-01"), "A": ("2024-06-01", "2024-09-01")})
    return {s: _agg(h, "1D") for s, h in HOURS.items()}


def test_run_reproduces_the_exit_test_and_counts_rule_x(setup):
    table, curves = lower_tf.run(setup, n=2, workers=0, load=fake_load, log=lambda *a: None)
    assert len(table) == 2 * len(lower_tf.runs())
    reference, _ = exits.run(setup, n=2, workers=1, log=lambda *a: None)
    assert lower_tf.validity(table, reference) == []
    broken = reference.assign(sharpe=reference.sharpe + 0.1)
    assert lower_tf.validity(table, broken)                      # a difference is reported
    x = table[(table.variant == "X") & (table.costs == "base")]
    assert (x.x_exits > 0).all()
    assert (table[table.variant == lower_tf.BASELINE].x_exits == 0).all()
    v = lower_tf.verdict(table)
    assert set(v) == {"E", "X"} and len(v["E"][0]) == 6


def test_in_position_ends_at_the_close_of_an_intraday_exit():
    days = pd.date_range("2024-01-01", periods=5, freq="D")
    trades = pd.DataFrame({"entry_time": [days[1]], "exit_time": [days[3] + pd.Timedelta(hours=5)]})
    assert lower_tf.in_position(trades, days).tolist() == [False, True, True, False, False]
    at_open = pd.DataFrame({"entry_time": [days[1]], "exit_time": [days[3]]})
    assert lower_tf.in_position(at_open, days).tolist() == sv.in_position(at_open, days).tolist()


def test_lower_frames_are_cut_to_the_assets_days():
    daily = _agg(HOURS["C0USDT"], "1D").iloc[10:20].reset_index(drop=True)
    frames = lower_tf.lower_frames("C0USDT#2", daily, fake_load)
    assert frames["1h"].timestamp.iloc[0] == daily.timestamp.iloc[0]
    assert frames["1h"].timestamp.iloc[-1] == daily.timestamp.iloc[-1] + pd.Timedelta(hours=23)
    assert len(frames["4h"]) == 10 * 6


def test_count_in_uses_each_months_members():
    m1, m2 = pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01")
    times = {"A": pd.DatetimeIndex(["2024-01-05", "2024-02-05"]), "B": pd.DatetimeIndex(["2024-01-07"])}
    assert lower_tf.count_in({m1: ["A", "B"], m2: ["B"]}, times, "2024-03-01") == 2
