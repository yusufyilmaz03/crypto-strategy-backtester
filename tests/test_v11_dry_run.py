import json

import pandas as pd
import pytest

from kripto_bot.analysis import v11_dry_run as dr
from kripto_bot.llm import provider as p
from kripto_bot.llm.store import LLMStore
from tests.test_llm_review import Answers, T, hold, snap

SYMBOLS = [f"C{i}/USDT" for i in range(7)]


def enter(symbol, limit, stop, tp):
    return dict(hold(symbol), action="enter", entry={"type": "limit", "price": limit, "expires_candles": 1},
                stop=stop, take_profit=tp, p_tp=0.5, grade="normal")


class Echo(p.Provider):
    """Answers hold for every symbol in the prompt; counts calls per symbol count."""

    def __init__(self, fail_after=None):
        self.calls, self.fail_after = [], fail_after

    def complete(self, prompt, schema):
        syms = [line.split(": ", 1)[1] for line in prompt.splitlines() if line.startswith("## Symbol: ")]
        self.calls.append(len(syms))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            return p.LLMResult(p.USAGE_LIMIT)
        return p.LLMResult(p.OK, output={"plans": [hold(s) for s in syms]})


def _snapshot(symbols, call_time, log=print):
    return snap(tuple(symbols))


def test_a_day_runs_the_comparison_first_then_the_load(tmp_path):
    st = LLMStore(str(tmp_path / "paper.db"))
    providers = {s: Echo() for s in dr.SETUPS}
    out = dr.run_day(T, SYMBOLS, st, providers, snapshot=_snapshot, log=lambda *_: None)
    assert list(out) == [(s, m) for s, m, _, _ in dr.RUNS]
    assert providers["astra-high"].calls == [1] * 7 + [1] * 7 + [5, 2] + [5, 2]
    assert providers["astra-medium"].calls == [5, 2] and providers["sol-high"].calls == [5, 2]
    calls = st.calls(dr.PREFIX + "%")
    assert set(calls["instance"]) == {"dry-run:astra-high", "dry-run:astra-medium", "dry-run:sol-high"}
    nonews = calls[calls["mode"] == "group-no-news"]
    assert all("Headlines mentioning" not in t for t in nonews["prompt"])


def test_a_usage_limit_ends_the_day(tmp_path):
    st = LLMStore(str(tmp_path / "paper.db"))
    providers = {s: Echo(fail_after=9 if s == "astra-high" else None) for s in dr.SETUPS}
    logs = []
    out = dr.run_day(T, SYMBOLS, st, providers, snapshot=_snapshot, log=logs.append)
    assert list(out) == [("astra-high", "single-a"), ("astra-high", "single-b")]
    assert providers["astra-medium"].calls == []
    assert any("usage limit" in line for line in logs)


ATR = 100.0


def _row(plan, accepted=True, last=3000.0):
    return {"plan": json.dumps(plan), "accepted": int(accepted), "context": json.dumps({"last": last, "atr": ATR})}


def test_decisions_and_agreement():
    a = dr.decision(_row(enter("X", 2950, 2850, 3200)))
    assert a == ("enter", 2950, 2850, 3200)
    assert dr.decision(_row(enter("X", 2950, 2850, 3200), accepted=False))[0] == "hold"
    assert dr.agree(a, ("enter", 2990, 2860, 3240), ATR)                  # all within 0.5 ATR
    assert not dr.agree(a, ("enter", 3010, 2850, 3200), ATR)              # entry 60 away
    assert not dr.agree(a, ("enter", 2950, 2850, None), ATR)              # one has no take-profit
    assert not dr.agree(a, ("hold", None, None, None), ATR)
    assert dr.agree(("hold", None, None, None), ("hold", None, None, None), ATR)


def _plans(rows):
    return pd.DataFrame([{"instance": "dry-run:astra-high", "time": "2026-11-10T00:00:05+00:00", "symbol": s,
                          "mode": m, **_row(plan, ok)} for s, m, plan, ok in rows])


def test_groups_are_chosen_when_they_agree_about_as_well_and_reject_no_more():
    rows = []
    for s in ("A", "B", "C", "D"):
        rows += [(s, "single-a", hold(s), True), (s, "single-b", hold(s), True), (s, "group", hold(s), True)]
    out = dr.comparison(_plans(rows))
    assert out["symbol_days"] == 4 and out["single-a vs single-b"] == 1.0 and out["use groups"]
    rows[-1] = ("D", "group", enter("D", 2950, 2850, 3200), True)          # groups disagree once: 25 points
    assert not dr.comparison(_plans(rows))["use groups"]
    rows[-1] = ("D", "group", hold("D"), False)                            # a rejection, single runs have none
    out = dr.comparison(_plans(rows))
    assert out["rejected group"] == 0.25 and not out["use groups"]


def test_next_run_is_just_after_the_daily_close():
    assert dr.seconds_to_next_run(pd.Timestamp("2026-11-09 23:59:00", tz="UTC")) == 65


def test_waiting_follows_the_wall_clock():
    times = iter(pd.date_range("2026-11-09 23:59:00", periods=10, freq="30s", tz="UTC"))
    now = [next(times)]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        now[0] = next(times)

    dr.wait_for_next_close(clock=lambda: now[0], sleep=sleep)
    assert now[0] >= pd.Timestamp("2026-11-10 00:00:05", tz="UTC") and len(sleeps) == 3


def test_a_close_missed_by_a_few_hours_is_made_up_once(tmp_path):
    st = LLMStore(str(tmp_path / "paper.db"))
    morning = pd.Timestamp("2026-11-10 02:00", tz="UTC")
    assert dr.missed_today(st, morning)
    assert not dr.missed_today(st, pd.Timestamp("2026-11-10 07:00", tz="UTC"))       # too late: wait
    dr.run_day(pd.Timestamp("2026-11-10 00:00:05", tz="UTC"), SYMBOLS[:1], st, {s: Echo() for s in dr.SETUPS},
               snapshot=_snapshot, log=lambda *_: None)
    assert not dr.missed_today(st, morning)                                          # already ran today
