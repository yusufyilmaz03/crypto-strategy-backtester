import pandas as pd
import pytest

from research import _portfolio, summarize


def _curve(values, start="2025-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="1D"), dtype=float)


def test_portfolio_is_equal_weight_average_of_returns():
    a = _curve([100, 110, 121])      # +10%, +10%
    b = _curve([50, 45, 45])         # -10%, 0%
    eq = _portfolio({"a": a, "b": b})
    assert eq.iloc[-1] == pytest.approx(1.0 * (1 + 0.0) * (1 + 0.05))


def test_summarize_flags_candidates():
    def res(values):
        eq = _curve(values)
        return {"oos_equity": eq, "high_cost_equity": eq * 0.99,
                "oos": {"return_pct": (values[-1] / values[0] - 1) * 100, "trades": 20},
                "default_return_pct": 0.0, "train_positive_share": 0.5}

    up = [1000 * 1.01 ** i for i in range(60)]
    flat_bh = _curve([10.0] * 60)
    per_symbol = {
        ("v1", "1h"): [("A", res(up), flat_bh), ("B", res(up), flat_bh)],
        ("v2", "1h"): [("A", res([1000.0] * 60), flat_bh), ("B", res([1000.0] * 60), flat_bh)],
    }
    s = summarize(per_symbol).set_index("Strategy")
    assert s.loc["v1", "Return%"] > 0 and s.loc["v1", "Trades"] == 40
    assert s.loc["v2", "Return%"] == pytest.approx(0.0)
    assert not s.loc["v2", "Pass"]
