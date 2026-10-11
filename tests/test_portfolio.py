import numpy as np
import pandas as pd
import pytest

from conftest import make_ohlcv
from kripto_bot import instances
from kripto_bot.core import sizing
from kripto_bot.trading import run_realtime
from kripto_bot.trading.store import PaperStore

DAYS = {"CALM/USDT": make_ohlcv(400, seed=1, start="2025-01-01", freq="1D"),
        "WILD/USDT": make_ohlcv(400, seed=2, start="2025-01-01", freq="1D")}
_wild = 100 * np.exp(np.cumsum(np.random.default_rng(5).normal(0.002, 0.04, 400)))
_calm = 100 * np.exp(np.cumsum(np.random.default_rng(6).normal(0.003, 0.01, 400)))   # rising: v9 holds it
for _c in ("open", "high", "low", "close"):
    DAYS["WILD/USDT"][_c] = _wild                     # four times as volatile as CALM
    DAYS["CALM/USDT"][_c] = _calm


@pytest.fixture
def clock(monkeypatch):
    state = {"n": 300}
    monkeypatch.setattr(run_realtime, "fetch_recent",
                        lambda s, tf, limit=200, **kw: DAYS[s].iloc[:state["n"]].tail(limit).reset_index(drop=True))
    monkeypatch.setattr(run_realtime, "now_ms",
                        lambda: int(DAYS["CALM/USDT"].timestamp.iloc[state["n"] - 1].value // 10**6) + 86_400_000)
    return state


def _portfolio(tmp_path):
    inst = instances.create({"name": "ivol", "strategy": "v9", "params": {"fast": 1, "slow": 50},
                             "timeframe": "1d", "symbols": list(DAYS), "equity": 1000,
                             "sizing": "inverse_vol"}, str(tmp_path / "i.json"))
    store = PaperStore(str(tmp_path / "paper.db"))
    runners = [run_realtime.SymbolRunner(s, "1d", "v9", inst.engine_config(), store,
                                         params=inst.params, label=inst.run_key) for s in inst.symbol_list]
    return inst, store, run_realtime.PortfolioRunner(runners, store, inst.run_key, "1d")


def _weights(store):
    return store.read("weights").assign(time=lambda d: pd.to_datetime(d.time))


def test_start_allocation_uses_inverse_volatility(tmp_path, clock):
    inst, store, pf = _portfolio(tmp_path)
    pf.update()
    w = _weights(store).set_index("symbol").weight
    t = DAYS["CALM/USDT"].timestamp.iloc[clock["n"] - 1]
    expect = sizing.inverse_vol_weights({s: d.set_index("timestamp").close.loc[:t] for s, d in DAYS.items()})
    assert w.to_dict() == pytest.approx(expect) and w["CALM/USDT"] > w["WILD/USDT"]
    equity = {r.symbol: r.engine.equity for r in pf.runners}
    assert sum(equity.values()) == pytest.approx(2000)                    # nothing traded yet: no cost
    assert equity["CALM/USDT"] == pytest.approx(2000 * w["CALM/USDT"])


def test_month_end_rebalance_with_catch_up_and_restart(tmp_path, clock):
    inst, store, pf = _portfolio(tmp_path)
    pf.update()                                         # start: 2025-10-27
    clock["n"] = 340                                    # weeks later, missed days caught up at once
    pf.update()
    w = _weights(store)
    month_ends = sorted(set(w.time))
    assert [t.strftime("%Y-%m-%d") for t in month_ends[1:]] == ["2025-10-31", "2025-11-30"]
    t = month_ends[1]
    expect = sizing.inverse_vol_weights({s: d.set_index("timestamp").close.loc[:t] for s, d in DAYS.items()})
    assert w[w.time == t].set_index("symbol").weight.to_dict() == pytest.approx(expect)   # only data up to t
    eq = store.read("equity").assign(time=lambda d: pd.to_datetime(d.time))
    at = eq[eq.time == t].set_index("symbol").equity
    assert (at / at.sum()).to_dict() == pytest.approx(expect, rel=1e-3)   # recorded value is post-rebalance

    # restart mid-month: state comes back, no extra rebalance
    clock["n"] = 345
    inst, store2, pf2 = _portfolio_reload(tmp_path, inst)
    pf2.update()
    assert set(_weights(store2).time) == set(month_ends)
    assert {r.symbol: r.last_time for r in pf2.runners} == {s: DAYS[s].timestamp.iloc[344] for s in DAYS}


def _portfolio_reload(tmp_path, inst):
    store = PaperStore(str(tmp_path / "paper.db"))
    runners = [run_realtime.SymbolRunner(s, "1d", "v9", inst.engine_config(), store,
                                         params=inst.params, label=inst.run_key) for s in inst.symbol_list]
    return inst, store, run_realtime.PortfolioRunner(runners, store, inst.run_key, "1d")


def test_only_held_symbols_pay_rebalance_costs(tmp_path, clock):
    inst, store, pf = _portfolio(tmp_path)
    pf.update()
    clock["n"] = 340
    pf.update()
    w = _weights(store)
    held_at = {}
    trades = store.read("trades")
    for t in sorted(set(w.time))[1:]:
        for s in DAYS:
            mine = trades[trades.symbol == s]
            held = ((pd.to_datetime(mine.entry_time) <= t) &
                    (mine.exit_time.isna() | (pd.to_datetime(mine.exit_time) > t))).any()
            cost = w[(w.time == t) & (w.symbol == s)].cost.iloc[0]
            assert (cost > 0) == bool(held)
            held_at[(t, s)] = held
    assert any(held_at.values())


def test_sizing_is_paper_only(tmp_path):
    with pytest.raises(instances.InstanceError, match="paper"):
        instances.create({"name": "tn", "mode": "testnet", "sizing": "inverse_vol"}, str(tmp_path / "i.json"))
    with pytest.raises(instances.InstanceError, match="sizing"):
        instances.create({"name": "xx", "sizing": "magic"}, str(tmp_path / "i.json"))
