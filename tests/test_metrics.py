import math

import pandas as pd
import pytest

from metrics import bars_per_year, compute_metrics, max_drawdown_pct, sharpe_ratio


def _equity(values, freq="1h"):
    idx = pd.date_range("2025-01-01", periods=len(values), freq=freq)
    return pd.Series(values, index=idx, dtype=float)


def _trades(rows):
    cols = ["entry_time", "exit_time", "pnl", "return_pct", "fees"]
    return pd.DataFrame(rows, columns=cols)


def test_bars_per_year():
    assert bars_per_year("1h") == 365 * 24
    assert bars_per_year("5m") == 365 * 24 * 12


def test_max_drawdown():
    assert max_drawdown_pct(_equity([100, 120, 90, 130, 104])) == pytest.approx(25.0)
    assert max_drawdown_pct(_equity([100, 101, 102])) == 0.0


def test_sharpe_matches_manual_formula():
    eq = _equity([1010, 1000, 1030, 1020, 1050])
    r = pd.Series([1000] + eq.tolist()).pct_change().dropna()
    expected = r.mean() / r.std(ddof=1) * math.sqrt(365 * 24)
    assert sharpe_ratio(eq, "1h", 1000) == pytest.approx(expected)
    assert math.isnan(sharpe_ratio(_equity([1000, 1000, 1000]), "1h", 1000))


def test_compute_metrics():
    eq = _equity([1000, 1100, 1100, 1045, 1045])
    t = eq.index
    trades = _trades([
        (t[0], t[1], 100.0, 10.0, 2.0),
        (t[2], t[3], -55.0, -5.0, 2.2),
    ])
    closes = pd.Series([50.0, 55.0, 55.0, 52.0, 60.0])
    m = compute_metrics(trades, eq, "1h", 1000.0, closes)
    assert m["return_pct"] == pytest.approx(4.5)
    assert m["buy_hold_pct"] == pytest.approx(20.0)
    assert m["trades"] == 2
    assert m["win_rate_pct"] == 50.0
    assert m["profit_factor"] == pytest.approx(100 / 55)
    assert m["avg_trade_pct"] == pytest.approx(2.5)
    assert m["avg_win_pct"] == 10.0 and m["avg_loss_pct"] == -5.0
    assert m["max_drawdown_pct"] == pytest.approx(5.0)
    assert m["exposure_pct"] == pytest.approx(80.0)
    assert m["fees"] == pytest.approx(4.2)


def test_compute_metrics_without_trades():
    eq = _equity([1000, 1000])
    m = compute_metrics(_trades([]), eq, "1h", 1000.0)
    assert m["trades"] == 0 and m["return_pct"] == 0.0
    assert math.isnan(m["win_rate_pct"]) and math.isnan(m["profit_factor"])


def test_profit_factor_without_losses_is_inf():
    eq = _equity([1000, 1100])
    trades = _trades([(eq.index[0], eq.index[1], 100.0, 10.0, 0.0)])
    assert compute_metrics(trades, eq, "1h", 1000.0)["profit_factor"] == math.inf
