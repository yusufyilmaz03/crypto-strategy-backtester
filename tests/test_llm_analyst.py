import json

import pandas as pd
import pytest

from kripto_bot import instances
from kripto_bot.app import run_all
from kripto_bot.llm import inputs
from kripto_bot.llm import prompt as pr
from kripto_bot.llm import provider as p
from kripto_bot.trading import llm_analyst as la
from kripto_bot.trading import order_sim as sim
from tests.test_llm_review import _daily, hold

DAY = pd.Timestamp("2026-11-10", tz="UTC")
SYMBOLS = ["ETH/USDT", "SOL/USDT"]


def enter_eth():
    return dict(hold("ETH/USDT"), action="enter", entry={"type": "limit", "price": 2950, "expires_candles": 2},
                stop=2850, take_profit=3150, p_tp=0.4, grade="strong", memo="bought the dip")


def watch_eth():
    return dict(hold("ETH/USDT"), review_if=[{"side": "below", "price": 2880}], memo="watch 2880")


class Clock:
    def __init__(self, t):
        self.t = pd.Timestamp(t)

    def __call__(self):
        return self.t


class Model(p.Provider):
    """Answers with plans[symbol] (a hold if missing) for every symbol in the prompt."""

    def __init__(self, plans=None, kind=p.OK):
        self.plans, self.kind, self.prompts = dict(plans or {}), kind, []

    def complete(self, prompt, schema):
        self.prompts.append(prompt)
        if self.kind != p.OK:
            return p.LLMResult(self.kind)
        syms = [line.split(": ", 1)[1] for line in prompt.splitlines() if line.startswith("## Symbol: ")]
        return p.LLMResult(p.OK, output={"plans": [self.plans.get(s, hold(s)) for s in syms]})


class Market:
    """1m candles by symbol; minutes() serves the closed ones from `since`."""

    def __init__(self):
        self.candles = {s: [] for s in SYMBOLS}

    def add(self, symbol, minute_of_day, o, h, l, day=DAY):
        self.candles[symbol].append(sim.Candle(day + pd.Timedelta(minutes=minute_of_day), o, h, l, o))

    def minutes(self, symbol, since, now):
        return [c for c in self.candles[symbol] if c.time >= since and c.time + pd.Timedelta(minutes=1) <= now]


def snapshot(symbols, now, log=print):
    syms = {s: pr.SymbolInput(s, 0.01, pr.market_summary(_daily(3000.0 if s == "ETH/USDT" else 150.0)))
            for s in symbols}
    return inputs.Snapshot(pd.Timestamp(now), pr.Context(pr.market_summary(_daily(98000.0))), syms, [])


def _inst(**kw):
    base = dict(name="v11-test", mode="paper", strategy="v11", timeframe="1d",
                params={"model": "gpt-6-astra", "effort": "high", "news": True, "group": 5},
                symbols=list(SYMBOLS), equity=1000.0, run_key="instance:v11-test")
    base.update(kw)
    return instances.Instance(**base)


def _analyst(tmp_path, model, market, clock, notes=None, **kw):
    return la.Analyst(_inst(**kw), provider=model, db_path=str(tmp_path / "paper.db"),
                      notify=(notes.append if notes is not None else lambda _: None), clock=clock,
                      sleep=lambda _: None, snapshot=snapshot, minutes=market.minutes, log=lambda *_: None)


def _rows(a, sql, *args):
    return a.llm.conn.execute(sql, args).fetchall()


def test_the_daily_pass_marks_equity_and_makes_one_scheduled_review(tmp_path):
    clock, market, model = Clock(DAY + pd.Timedelta(seconds=30)), Market(), Model({"ETH/USDT": enter_eth()})
    a = _analyst(tmp_path, model, market, clock)
    a.tick()
    assert len(model.prompts) == 1 and "## Symbol: SOL/USDT" in model.prompts[0]      # one group of two
    eq = _rows(a, "SELECT symbol, time, equity FROM equity WHERE strategy='instance:v11-test' ORDER BY symbol")
    assert eq == [("ETH/USDT", "2026-11-09T00:00:00", 1000.0), ("SOL/USDT", "2026-11-09T00:00:00", 1000.0)]
    assert a.books["ETH/USDT"].entry.price == 2950
    clock.t += pd.Timedelta(minutes=5)
    a.tick()
    assert len(model.prompts) == 1                                                    # once per day


def test_a_fill_and_a_stop_become_a_trade_with_its_forecast(tmp_path):
    clock, market = Clock(DAY + pd.Timedelta(seconds=30)), Market()
    a = _analyst(tmp_path, Model({"ETH/USDT": enter_eth()}), market, clock)
    a.tick()
    market.add("ETH/USDT", 1, 2960, 2965, 2940)        # fills the 2950 limit
    market.add("ETH/USDT", 2, 2900, 2905, 2840)        # through the 2850 stop
    clock.t = DAY + pd.Timedelta(minutes=3, seconds=5)
    a.tick()
    trade = _rows(a, "SELECT side, entry_price, reason, grade, entry_time, exit_time FROM trades "
                     "WHERE strategy='instance:v11-test'")
    assert trade == [("LONG", 2950.0, "STOP", "strong", "2026-11-10T00:01:00", "2026-11-10T00:02:00")]
    assert _rows(a, "SELECT symbol, fill_price, stop, take_profit, p_tp FROM llm_forecasts") == \
        [("ETH/USDT", 2950.0, 2850.0, 3150.0, 0.4)]
    assert [k for (k,) in _rows(a, "SELECT kind FROM llm_events WHERE symbol='ETH/USDT' ORDER BY id")] == ["entry", "stop"]


def test_the_next_prompt_shows_the_state_history_and_memo(tmp_path):
    clock, market, model = Clock(DAY + pd.Timedelta(seconds=30)), Market(), Model({"ETH/USDT": enter_eth()})
    a = _analyst(tmp_path, model, market, clock)
    a.tick()
    market.add("ETH/USDT", 1, 2960, 2965, 2940)
    clock.t = DAY + pd.Timedelta(days=1, seconds=30)
    a.tick()
    eth = model.prompts[1][model.prompts[1].index("## Symbol: ETH/USDT"):]
    assert "- Position: long since 2026-11-10 00:01 UTC at 2950, stop 2850, take-profit 3150" in eth
    assert "enter limit 2950, stop 2850, take-profit 3150 → filled on 2026-11-10T00:01" in eth
    assert "Your memo from the last call: 'bought the dip'" in eth


def test_a_review_level_calls_the_model_for_that_symbol(tmp_path):
    clock, market, model = Clock(DAY + pd.Timedelta(seconds=30)), Market(), Model({"ETH/USDT": watch_eth()})
    a = _analyst(tmp_path, model, market, clock)
    a.tick()
    market.add("ETH/USDT", 120, 2900, 2905, 2870)
    clock.t = DAY + pd.Timedelta(minutes=121, seconds=5)
    a.tick()
    assert len(model.prompts) == 2 and "(triggered review)" in model.prompts[1]
    assert "## Symbol: SOL/USDT" not in model.prompts[1]
    calls = _rows(a, "SELECT kind, symbols FROM llm_calls ORDER BY id")
    assert calls[-1] == ("triggered", '["ETH/USDT"]')


def _reach(a, market, clock, minute):
    """A wick through the 2880 level that closes back at 2950, so a renewed 2880 level is valid."""
    market.add("ETH/USDT", minute, 2950, 2955, 2870)
    clock.t = DAY + pd.Timedelta(minutes=minute + 1, seconds=5)
    a.tick()


def _skipped(a):
    return [json.loads(d)["reason"] for (d,) in _rows(a, "SELECT data FROM llm_events WHERE kind='review_skipped'")]


def test_no_trigger_within_an_hour_of_the_last_call_and_the_level_is_used_up(tmp_path):
    clock, market, model = Clock(DAY + pd.Timedelta(seconds=30)), Market(), Model({"ETH/USDT": watch_eth()})
    a = _analyst(tmp_path, model, market, clock)
    a.tick()                                                         # scheduled call at 00:00:30
    _reach(a, market, clock, 30)
    assert _skipped(a) == ["within 60 minutes of the last call"]
    _reach(a, market, clock, 100)                                    # the level triggered once; it is gone
    assert len(model.prompts) == 1


def test_at_most_three_triggered_reviews_a_day(tmp_path):
    clock, market, model = Clock(DAY + pd.Timedelta(seconds=30)), Market(), Model({"ETH/USDT": watch_eth()})
    a = _analyst(tmp_path, model, market, clock)
    a.tick()
    for minute in (100, 200, 300, 400):                              # each answer re-arms the level
        _reach(a, market, clock, minute)
    kinds = [k for (k,) in _rows(a, "SELECT kind FROM llm_calls ORDER BY id")]
    assert kinds == ["scheduled", "triggered", "triggered", "triggered"]
    assert _skipped(a) == ["3 triggered reviews today"]


def test_a_level_reached_while_the_bot_was_away_is_not_made_up(tmp_path):
    clock, market, model = Clock(DAY + pd.Timedelta(seconds=30)), Market(), Model({"ETH/USDT": watch_eth()})
    a = _analyst(tmp_path, model, market, clock)
    a.tick()
    market.add("ETH/USDT", 120, 2900, 2905, 2870)
    clock.t = DAY + pd.Timedelta(minutes=140)                         # woke 19 minutes later
    a.tick()
    assert len(model.prompts) == 1


def test_a_usage_limit_stops_the_calls_until_the_next_day(tmp_path):
    clock, market, notes = Clock(DAY + pd.Timedelta(seconds=30)), Market(), []
    model = Model(kind=p.USAGE_LIMIT)
    a = _analyst(tmp_path, model, market, clock, notes)
    a.tick()
    assert any("usage limit" in n for n in notes)
    clock.t += pd.Timedelta(hours=2)
    a.tick()
    assert len(model.prompts) == 1
    model.kind = p.OK
    clock.t = DAY + pd.Timedelta(days=1, seconds=30)
    a.tick()
    assert len(model.prompts) == 2


def test_exit_only_skips_new_entries(tmp_path):
    clock, market = Clock(DAY + pd.Timedelta(seconds=30)), Market()
    a = _analyst(tmp_path, Model({"ETH/USDT": enter_eth()}), market, clock, exit_only=True)
    a.tick()
    assert a.books["ETH/USDT"].entry is None
    assert _rows(a, "SELECT kind FROM llm_events WHERE symbol='ETH/USDT'") == [("enter_skipped",)]


def test_a_restart_resumes_the_books_without_repeating_candles(tmp_path):
    clock, market = Clock(DAY + pd.Timedelta(seconds=30)), Market()
    a = _analyst(tmp_path, Model({"ETH/USDT": enter_eth()}), market, clock)
    a.tick()
    market.add("ETH/USDT", 1, 2960, 2965, 2940)
    clock.t = DAY + pd.Timedelta(minutes=2, seconds=5)
    a.tick()
    a.close()
    b = _analyst(tmp_path, Model(), market, clock)
    assert b.books["ETH/USDT"].position.entry_price == 2950
    market.add("ETH/USDT", 2, 3200, 3210, 3190)                      # take-profit after the restart
    clock.t = DAY + pd.Timedelta(minutes=3, seconds=5)
    b.tick()
    trades = _rows(b, "SELECT reason FROM trades WHERE strategy='instance:v11-test'")
    assert trades == [("TAKE_PROFIT",)]


# ---------- the instance ----------

def test_v11_instances_are_validated_and_run_the_analyst(tmp_path):
    path = str(tmp_path / "instances.json")
    inst = instances.create({"name": "v11-astra-high", "strategy": "v11", "timeframe": "1d", "symbols": SYMBOLS,
                             "params": {"model": "gpt-6-astra", "effort": "high"}}, path)
    assert inst.params == {"model": "gpt-6-astra", "effort": "high", "news": True, "group": 5}
    assert run_all.instance_command(inst)[-3:] == ["kripto_bot.trading.llm_analyst", "--instance", "v11-astra-high"]


@pytest.mark.parametrize("data,why", [
    ({"params": {"model": "m", "effort": "huge"}}, "effort"),
    ({"params": {"model": "", "effort": "high"}}, "model"),
    ({"params": {"model": "m", "effort": "high", "temperature": 0}}, "no parameter"),
    ({"params": {"model": "m", "effort": "high", "group": 0}}, "group"),
    ({"symbols": None}, "fixed symbol list"),
    ({"timeframe": "4h"}, "timeframe 1d"),
    ({"mode": "testnet"}, "paper"),
])
def test_bad_v11_setups_are_refused(tmp_path, data, why):
    base = {"name": "v11-x", "strategy": "v11", "timeframe": "1d", "symbols": SYMBOLS,
            "params": {"model": "m", "effort": "high"}}
    with pytest.raises(instances.InstanceError, match=why):
        instances.create({**base, **data}, str(tmp_path / "instances.json"))
