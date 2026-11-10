import json

import numpy as np
import pandas as pd
import pytest

from kripto_bot.analysis import v11_forecasts as vf
from kripto_bot.llm.store import LLMStore
from kripto_bot.trading import llm_analyst

T0 = pd.Timestamp("2026-12-01", tz="UTC")


def _mins(*rows, start=T0):
    """1m candles from `start`: rows of (high, low)."""
    return pd.DataFrame({"time": [start + pd.Timedelta(minutes=i) for i in range(len(rows))],
                         "high": [h for h, _ in rows], "low": [l for _, l in rows]})


@pytest.mark.parametrize("rows,expected", [
    ([(101, 99), (106, 99)], (1, "take_profit")),
    ([(101, 99), (101, 90)], (0, "stop")),
    ([(101, 99), (106, 90)], (0, "stop")),                 # both in one candle: stop first
    ([(106, 99), (101, 99)], (0, "neither")),              # the fill candle's high doesn't count
    ([(101, 90), (106, 99)], (0, "stop")),                 # its low does
    ([(105, 99), (101, 95)], (0, "stop")),                 # 105 is not above 105; 95 reaches the stop
])
def test_resolve(rows, expected):
    assert vf.resolve(_mins(*rows), T0, stop=95, take_profit=105)[:2] == expected


def test_resolve_ignores_candles_after_30_days():
    late = _mins((101, 99), start=T0)
    late = pd.concat([late, _mins((110, 99), start=T0 + pd.Timedelta(days=30))])
    assert vf.resolve(late, T0, 95, 105)[:2] == (0, "neither")


def test_p0():
    assert vf.p0(100, 95, 110) == pytest.approx(1 / 3)


def _random_walk(days, seed=1):
    rng = np.random.default_rng(seed)
    n = days * 1440
    price = 100 * np.exp(np.cumsum(rng.normal(0, 0.0008, n)))
    times = pd.date_range("2026-01-01", periods=n, freq="min", tz="UTC")
    mins = pd.DataFrame({"time": times, "high": price * 1.0002, "low": price * 0.9998, "close": price})
    daily = mins.set_index("time").resample("1D").agg({"high": "max", "low": "min", "close": "last"}).reset_index()
    return mins, daily


def test_placebo_is_near_the_driftless_chance_on_a_random_walk():
    mins, daily = _random_walk(120)
    rate, n = vf.placebo_rate(daily, mins, k_stop=1.0, k_tp=2.0,
                              start=pd.Timestamp("2026-01-20", tz="UTC"), end=pd.Timestamp("2026-04-30", tz="UTC"))
    assert n > 60
    assert 0.1 < rate < 0.55                    # 1/3 without drift, minus the closes that time out


def test_placebo_uses_only_closes_with_30_days_after_them():
    mins, daily = _random_walk(60)
    _, n = vf.placebo_rate(daily, mins, 1.0, 1.0, start=pd.Timestamp("2026-01-20", tz="UTC"),
                           end=pd.Timestamp("2026-03-01", tz="UTC"))
    assert n == 11                              # closes 01-20 ... 01-30: the last one's 30 days end on 03-01


def test_brier_skill_and_calibration():
    p, o = [0.8, 0.2, 0.6, 0.4], [1, 0, 1, 0]
    assert vf.brier(p, o) == pytest.approx((0.04 + 0.04 + 0.16 + 0.16) / 4)
    assert vf.skill(p, [0.5] * 4, o) == pytest.approx(1 - 0.1 / 0.25)
    cal = {r["bin"]: r for r in vf.calibration(p, o)}
    assert cal["0.8-1.0"]["n"] == 1 and cal["0.8-1.0"]["observed"] == 1.0
    assert cal["0.0-0.2"]["n"] == 0


def _scored(n, seed=0):
    rng = np.random.default_rng(seed)
    p = rng.uniform(0.2, 0.8, n)
    return pd.DataFrame({"p_tp": p, "outcome": (rng.uniform(size=n) < p).astype(int), "how": "stop",
                         "p0": 0.4, "p_placebo": 0.4, "plan_date": [f"2026-12-{1 + i % 20:02d}" for i in range(n)]})


def test_below_30_scored_plans_only_counts_are_reported():
    out = vf.score(_scored(10))
    assert out["scored"] == 10 and "brier p_tp" not in out and out["share stop"] == 1.0


def test_full_score_with_a_bootstrap_interval():
    out = vf.score(_scored(200))
    assert out["skill vs p_placebo"] > 0                          # the forecasts carry information
    lo, hi = out["skill vs p_placebo 90%"]
    assert lo < out["skill vs p_placebo"] < hi
    assert sum(r["n"] for r in out["calibration"]) == 200


def test_evaluate_reads_the_database_and_applies_the_cut_off(tmp_path):
    db = str(tmp_path / "paper.db")
    st = LLMStore(db)
    st.conn.executescript(llm_analyst.SCHEMA)
    ctx = json.dumps({"last": 100.0, "atr": 5.0, "tick": 0.01})
    for i, day in enumerate(["2026-12-01", "2026-12-20"]):         # the second fill is too recent
        plan = {"action": "enter", "take_profit": 110}
        st.conn.execute("INSERT INTO llm_plans (id, instance, time, symbol, action, accepted, plan, context) "
                        "VALUES (?, 'v', ?, 'X/USDT', 'enter', 1, ?, ?)", (i + 1, day + "T00:00:05+00:00",
                                                                         json.dumps(plan), ctx))
        st.conn.execute("INSERT INTO llm_forecasts VALUES ('v', ?, 'X/USDT', ?, 100, 95, 110, 0.4)",
                        (i + 1, day + "T00:01:00+00:00"))
    st.conn.execute("INSERT INTO llm_plans (instance, symbol, action, accepted, plan) VALUES "
                    "('v', 'X/USDT', 'enter', 1, '{\"take_profit\": null}')")
    st.conn.commit()
    st.close()
    mins = _mins((101, 99), (111, 99), start=pd.Timestamp("2026-12-01 00:01", tz="UTC"))
    daily = pd.DataFrame({"time": pd.date_range("2026-11-01", periods=40, freq="D", tz="UTC"),
                          "high": 102.0, "low": 98.0, "close": 100.0})
    logs = []
    out = vf.evaluate("v", "2027-01-05", db, minutes=lambda *a: mins, daily=lambda *a: daily, log=logs.append)
    assert out["scored"] == 1 and out["share take_profit"] == 1.0
    assert "1 of 3 accepted enter plans had no take-profit" in logs[0]
