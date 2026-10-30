import pandas as pd
import pytest

from kripto_bot.analysis import filters, lower_tf
from kripto_bot.analysis import survivorship as sv
from test_lower_tf_test import HOURS, _agg, fake_load


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(sv, "PERIODS", {"B": ("2024-04-01", "2024-06-01"), "A": ("2024-06-01", "2024-09-01")})
    return {s: _agg(h, "1D") for s, h in HOURS.items()}


def test_run_passes_its_validity_checks_and_judges_trades(setup):
    table, _ = filters.run(setup, n=2, workers=0, load=fake_load, log=lambda *a: None)
    reference, _ = lower_tf.run(setup, n=2, workers=0, load=fake_load, log=lambda *a: None)
    assert filters.validity(table, reference) == []
    broken = reference.assign(max_dd_pct=reference.max_dd_pct + 1)
    assert filters.validity(table, broken)
    base = table[(table.variant == filters.BASELINE) & (table.costs == "base")]
    assert base.trades.sum() > 0 and base.win_rate_pct.dropna().between(0, 100).all()
    s200 = table[(table.variant == "S200") & (table.costs == "base")]
    assert (s200.trades.values <= base.trades.values).all()                      # the filter only removes entries
    v = filters.verdict(table)
    assert set(v) == set(filters.PRIMARY) and all(len(c) == 14 for c in v.values())


def test_trade_stats():
    s = filters.trade_stats(pd.DataFrame({"return_pct": [10.0, -5.0, 2.0, -1.0, 0.0]}))
    assert s["trades"] == 5 and s["win_rate_pct"] == 40.0
    assert s["profit_factor"] == pytest.approx(12 / 6) and s["expectancy_pct"] == pytest.approx(1.2)
    assert s["avg_win_pct"] == 6.0 and s["avg_loss_pct"] == pytest.approx(-2.0)


def test_member_trades_by_entry_month_and_period_end():
    m1, m2 = pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01")
    t = pd.DataFrame({"entry_time": pd.to_datetime(["2024-01-03", "2024-02-03", "2024-02-20", "2024-02-25"]),
                      "exit_time": pd.to_datetime(["2024-01-10", "2024-02-10", "2024-03-05", "2024-02-28"]),
                      "return_pct": [1.0, 2.0, 3.0, 4.0], "reason": ["SIGNAL", "SIGNAL", "SIGNAL", "END"]})
    got = filters.member_trades({m1: ["A"], m2: ["B"]}, {"A": t, "B": t}, "2024-03-01")
    # A in January: the first trade; B in February: the second (the third exits after the end, the fourth is END)
    assert got.return_pct.tolist() == [1.0, 2.0]
