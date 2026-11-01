import numpy as np
import pandas as pd
import pytest

from conftest import make_ohlcv
from kripto_bot import instances
from kripto_bot.core import bankroll
from kripto_bot.core.bankroll import BankrollConfig, Holding
from kripto_bot.core.engine import Engine, EngineConfig
from kripto_bot.core.indicators import add_indicators
from kripto_bot.trading import run_realtime
from kripto_bot.trading.store import PaperStore


# ---------------------------------------------------------------- scores and grades
def test_features_use_no_later_candles():
    df = add_indicators(make_ohlcv(400, seed=3, freq="1D"))
    full = bankroll.features(df, 50, 200)
    for n in (260, 330, 399):
        part = bankroll.features(df.iloc[:n].copy(), 50, 200)
        pd.testing.assert_series_equal(full.iloc[n - 1], part.iloc[-1])


def test_scores_points_and_ranks():
    row = lambda **kw: pd.Series({"above_trend": True, "distance": 1.0, "slope": 0.0025, "ret": 0.1,
                                  "breakout": False, "volume": 1.0, "rsi": 60.0, "atr_pct": 0.05, **kw})
    rows = {"A": row(), "B": row(ret=0.3, above_trend=False, distance=5.0, atr_pct=0.02)}
    sc = bankroll.scores(rows, ["trend_sma"])
    assert sc == {"A": 100.0, "B": 0.0}
    assert bankroll.scores(rows, ["distance"]) == {"A": 50.0, "B": 50.0}          # 1 ATR: half; 5 ATR: stretched
    assert bankroll.scores(rows, ["slope", "rsi"])["A"] == 75.0                    # 0.5 and 1
    assert bankroll.scores(rows, ["momentum"]) == {"A": 50.0, "B": 100.0}         # ranks among the symbols
    assert bankroll.scores(rows, ["low_vol"]) == {"A": 50.0, "B": 100.0}
    assert bankroll.scores(rows, ["lower_tf"], lower={"A": 0.5, "B": None}) == {"A": 50.0, "B": 0.0}
    assert bankroll.scores(rows, ["rs_btc"], btc_ret=0.2) == {"A": 0.0, "B": 100.0}
    assert bankroll.scores(rows, ["volume"])["A"] == 50.0
    assert bankroll.scores({"A": row(distance=float("nan"))}, ["distance"]) == {"A": 0.0}
    assert [bankroll.grade(x) for x in (0, 39.9, 40, 60, 79.9, 80, 100)] == [0, 0, 1, 2, 2, 3, 3]


# ---------------------------------------------------------------- funding
def test_fund_strongest_first_cut_to_free_cash():
    cfg = BankrollConfig(stakes=(0, 3, 5, 8), min_entry=10, max_entry=600)
    out, left = bankroll.fund(cfg, 10_000, 1200, [("PEPE", 31), ("LINK", 52), ("DOGE", 45), ("SOL", 86), ("AVAX", 71)])
    got = {f.symbol: (round(f.amount, 2), f.reason) for f in out}
    assert [f.symbol for f in out] == ["SOL", "AVAX", "LINK", "DOGE", "PEPE"]
    assert got["SOL"] == (600, None)                      # 8% = 800, capped at the maximum entry
    assert got["AVAX"] == (500, None)
    assert got["LINK"][0] == 100 and "cut to the free cash" in got["LINK"][1]
    assert got["DOGE"] == (0, "no free cash")
    assert got["PEPE"] == (0, "grade Weak: stake 0%")
    assert left == pytest.approx(0)


def test_stake_below_minimum_entry_is_skipped():
    cfg = BankrollConfig(stakes=(1, 1, 1, 1), min_entry=20)
    assert bankroll.stake(cfg, 1000, 1, 5000) == (0.0, "stake 10.00 USDT below the minimum entry")


# ---------------------------------------------------------------- rotation choice
def _h(symbol, score, price=110.0, cost=1000.0, held=5, busy=False):
    return Holding(symbol, score, 10.0, price, cost, held, busy)


def test_rotation_plan_picks_weakest_eligible():
    cfg = BankrollConfig()
    hs = [_h("NEW", 50, held=1), _h("BUSY", 10, busy=True), _h("WEAK", 40), _h("WEAKER", 30), _h("CLOSE", 75)]
    plan = bankroll.rotation_plan(cfg, 1500, 90, hs)
    assert [p.symbol for p in plan] == ["WEAKER", "WEAK"] and all(p.fraction == 1 for p in plan)
    assert bankroll.rotation_plan(cfg, 5000, 90, hs) == []                     # can't free enough


def test_rotation_profit_only_and_trim():
    hs = [_h("LOSER", 10, price=90.0), _h("WINNER", 30, price=120.0)]
    plan = bankroll.rotation_plan(BankrollConfig(profit_only=True), 500, 90, hs)
    assert [p.symbol for p in plan] == ["WINNER"] and plan[0].fraction == 1
    plan = bankroll.rotation_plan(BankrollConfig(trim=True), 500, 90, hs)
    assert plan[0].symbol == "LOSER" and plan[0].fraction == pytest.approx(500 / BankrollConfig().exit_value(10, 90))


def test_low_state_has_hysteresis():
    assert bankroll.low_state("value", 70, 690, 0, 1000)
    assert not bankroll.low_state("value", 70, 710, 0, 1000)
    assert bankroll.low_state("value", 70, 740, 0, 1000, was_low=True)        # needs 75 to recover
    assert bankroll.low_state("free", 10, 5000, 50, 1000)
    assert bankroll.twr_step(1.0, 1000, 1600, 500) == pytest.approx(1.1)        # the deposit isn't return


# ---------------------------------------------------------------- engine
def test_engine_trim_and_lot_rounding():
    e = Engine(EngineConfig(fee_rate=0.001, slippage_bps=0, atr_multiplier=None, initial_equity=0), "X")
    e.lot_step, e.min_cost = 0.01, 5.0
    e.equity = 100.0
    e.queue_entry()
    e.on_bar(pd.Timestamp("2025-01-01"), 3.0, 3.1, 2.9, 3.0, "-", entry_ok=False)
    p = e.position
    assert p.qty == pytest.approx(33.3) and e.leftover == pytest.approx(100 - 33.3 * 3 * 1.001)   # 33.31 costs > 100
    e.queue_exit("ROTATE", 0.5)
    e.on_bar(pd.Timestamp("2025-01-02"), 4.0, 4.1, 3.9, 4.0, "-", entry_ok=False)
    t = e.trades[-1]
    assert t.reason == "TRIM" and t.qty == pytest.approx(16.65) and e.position.qty == pytest.approx(16.65)
    assert e.released == pytest.approx(16.65 * 4 * 0.999)
    assert e.equity == pytest.approx(e.position.equity_before)
    e.queue_exit("ROTATE")
    e.on_bar(pd.Timestamp("2025-01-03"), 4.0, 4.1, 3.9, 4.0, "BUY", entry_ok=True)
    assert e.trades[-1].reason == "ROTATE" and e.position is None
    assert e.pending is None and e.reentry_above is not None                  # no buying straight back


def test_entry_below_minimum_order_is_not_opened():
    e = Engine(EngineConfig(fee_rate=0.001, slippage_bps=0, atr_multiplier=None, initial_equity=0), "X")
    e.lot_step, e.min_cost = 1.0, 5.0
    e.equity = 8.0
    e.queue_entry()
    e.on_bar(pd.Timestamp("2025-01-01"), 6.0, 6.1, 5.9, 6.0, "-", entry_ok=False)   # 1 unit = 6 USDT but 8/6.006 rounds to 1
    assert e.position is not None and e.position.qty == 1
    e2 = Engine(EngineConfig(fee_rate=0.001, slippage_bps=0, atr_multiplier=None, initial_equity=0), "X")
    e2.lot_step, e2.min_cost = 1.0, 5.0
    e2.equity = 8.0
    e2.queue_entry()
    e2.on_bar(pd.Timestamp("2025-01-01"), 9.0, 9.1, 8.9, 9.0, "-", entry_ok=False)  # rounds to 0 units
    assert e2.position is None and e2.equity == 8.0


# ---------------------------------------------------------------- the paper bot
N = 360
_t = np.arange(N)
_up = 100 * np.exp(0.002 * _t + 0.01 * np.sin(_t / 3))           # rising: v9 says BUY
DAYS = {}
for _name, _k in [("AAA/USDT", 1.0), ("BBB/USDT", 1.3), ("CCC/USDT", 0.7)]:
    _df = make_ohlcv(N, seed=int(_k * 10), start="2025-01-01", freq="1D")
    _c = 100 * np.exp(0.002 * _k * _t + 0.01 * np.sin(_t / 3 + _k))
    _df["open"] = np.r_[_c[0], _c[:-1]]
    _df["close"] = _c
    _df["high"] = np.maximum(_df["open"], _c) * 1.01
    _df["low"] = np.minimum(_df["open"], _c) * 0.99
    DAYS[_name] = _df


@pytest.fixture
def clock(monkeypatch):
    state = {"n": 300}
    monkeypatch.setattr(run_realtime, "fetch_recent",
                        lambda s, tf, limit=200, **kw: DAYS[s].iloc[:state["n"]].tail(limit).reset_index(drop=True))
    monkeypatch.setattr(run_realtime, "now_ms",
                        lambda: int(DAYS["AAA/USDT"].timestamp.iloc[state["n"] - 1].value // 10**6) + 86_400_000)
    return state


def _bankroll(tmp_path, **setup):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = str(tmp_path / "i.json")
    items = instances.load_all(path)
    if "br" in items:
        inst = items["br"]
    else:
        inst = instances.create({"name": "br", "strategy": "v9", "params": {"fast": 1, "slow": 50},
                                 "timeframe": "1d", "symbols": list(DAYS), "equity": 1000, "sizing": "bankroll",
                                 "score_inputs": ["trend_sma", "distance", "momentum"], **setup}, path)
    store = PaperStore(str(tmp_path / "paper.db"))
    runners = [run_realtime.SymbolRunner(s, "1d", "v9", inst.engine_config(), store, params=inst.params,
                                         label=inst.run_key) for s in inst.symbol_list]
    return inst, store, run_realtime.BankrollRunner(runners, store, inst, rules={})


def _check_books(store, br):
    """Free cash = the ledger's sum; value = free + reserved + invested."""
    led = store.read("bankroll_ledger")
    assert led.amount.sum() == pytest.approx(br.state["free"])
    b = br.balances()
    assert b["value"] == pytest.approx(b["free"] + b["reserved"] + b["invested"])
    assert br.state["free"] >= -1e-9


def test_bankroll_funds_entries_and_keeps_the_books(tmp_path, clock):
    inst, store, br = _bankroll(tmp_path, stakes=[0, 10, 20, 30])
    br.update()                                         # start: the bankroll only
    assert br.state["free"] == 1000 and br.state["contributed"] == 1000
    for n in range(301, 330):
        clock["n"] = n
        br.update()
        _check_books(store, br)
    trades = store.read("trades")
    assert len(trades) and trades.score.notna().all() and trades.grade.notna().all()
    sig = store.read("signals")
    assert sig.score.notna().all() and sig.action.str.contains("stake|not funded|already in position").any()
    led = store.read("bankroll_ledger")
    assert {"INITIAL", "RESERVE"} <= set(led.kind)
    # stakes are % of the contributed capital, so no single entry is above 30% of 1000
    assert (-led[led.kind == "RESERVE"].amount <= 300 + 1e-9).all()
    snaps = store.read("bankroll_snapshots")
    assert len(snaps) == 30 and snaps.twr.iloc[0] == 1.0
    # engines never hold a whole sleeve: flat ones hold 0 or a reserved stake
    assert all(r.engine.equity <= 300 + 1e-9 or r.engine.position is not None for r in br.runners)


def test_bankroll_restart_gives_the_same_books(tmp_path, clock):
    inst, store, br = _bankroll(tmp_path / "a", stakes=[0, 10, 20, 30])
    for n in range(300, 330):
        clock["n"] = n
        br.update()
    straight = store.read("bankroll_ledger")[["kind", "symbol", "amount"]]

    clock["n"] = 300
    inst, store2, br2 = _bankroll(tmp_path / "b", stakes=[0, 10, 20, 30])
    for n in range(300, 315):
        clock["n"] = n
        br2.update()
    inst, store2, br2 = _bankroll(tmp_path / "b")       # restart: states come from the database
    for n in (322, 329):                                # with missed days caught up at once
        clock["n"] = n
        br2.update()
    again = store2.read("bankroll_ledger")[["kind", "symbol", "amount"]]
    pd.testing.assert_frame_equal(straight, again)


def test_deposit_and_withdrawal(tmp_path, clock):
    inst, store, br = _bankroll(tmp_path)
    br.apply_requests()                                 # nothing yet
    store.request_bankroll(inst.run_key, "1d", "deposit", 500, "top up")
    br.apply_requests()                                 # not started: waits
    assert store.bankroll_requests(inst.run_key, "1d")
    br.update()
    br.apply_requests()
    assert br.state["contributed"] == 1500 and br.state["free"] == 1500 and br.state["flow"] == 500
    store.request_bankroll(inst.run_key, "1d", "withdraw", 2000)
    store.request_bankroll(inst.run_key, "1d", "withdraw", 200)
    br.apply_requests()
    req = store.read("bankroll_requests")
    assert list(req.result) == ["done", req.result.iloc[1], "done"] and req.result.iloc[1].startswith("refused")
    assert br.state["contributed"] == 1300 and br.state["flow"] == 300
    clock["n"] = 301
    br.update()
    snaps = store.read("bankroll_snapshots")
    assert snaps.twr.iloc[-1] == pytest.approx(1.0)        # deposits and withdrawals aren't return
    led = store.read("bankroll_ledger")
    assert list(led.kind[:3]) == ["INITIAL", "DEPOSIT", "WITHDRAW"] and led.note.iloc[1] == "top up"


@pytest.fixture
def fixed_scores(monkeypatch):
    """Scores by symbol, changed by the test between updates."""
    table = {}
    monkeypatch.setattr(run_realtime.bankroll, "scores", lambda rows, *a, **k: {s: table.get(s, 0.0) for s in rows})
    return table


@pytest.mark.parametrize("trim", [False, True])
def test_rotation_sells_a_weaker_position_for_a_very_strong_signal(tmp_path, clock, fixed_scores, trim):
    inst, store, br = _bankroll(tmp_path, stakes=[0, 0, 85, 85], rotation=True, rotate_trim=trim)
    fixed_scores.update({"AAA/USDT": 70, "BBB/USDT": 10, "CCC/USDT": 10})
    for n in range(300, 306):                           # AAA gets 850; the others aren't funded (Weak)
        clock["n"] = n
        br.update()
    assert br.by_symbol["AAA/USDT"].engine.position is not None
    fixed_scores.update({"AAA/USDT": 50, "BBB/USDT": 95})
    clock["n"] = 306
    br.update()                                         # BBB: very strong, 150 free -> rotate AAA
    led = store.read("bankroll_ledger")
    assert (led.kind == "ROTATE").sum() == 1
    funded = led[(led.kind == "RESERVE") & (led.symbol == "BBB/USDT")].iloc[-1]
    assert funded.note.startswith("150.00 from free cash + ")          # free cash first, rotation for the rest
    assert -funded.amount == pytest.approx(850)
    rot = led[led.kind == "ROTATE"].iloc[0]
    assert ("reduce by" in rot.note) == trim                          # reduced only by what is still needed
    clock["n"] = 307
    br.update()
    trades = store.read("trades")
    reasons = set(trades.reason.dropna())
    assert ("TRIM" in reasons) == trim and ("ROTATE" in reasons) != trim
    assert br.by_symbol["BBB/USDT"].engine.position is not None
    assert (br.by_symbol["AAA/USDT"].engine.position is not None) == trim
    _check_books(store, br)
    if trim:                                            # the open trade's quantity follows the engine
        open_ = trades[trades.symbol.eq("AAA/USDT") & trades.exit_time.isna()]
        assert open_.qty.iloc[0] == pytest.approx(br.by_symbol["AAA/USDT"].engine.position.qty)


def test_rotation_profit_only_keeps_losers(tmp_path, clock, fixed_scores, monkeypatch):
    inst, store, br = _bankroll(tmp_path, stakes=[0, 0, 85, 85], rotation=True, rotate_profit_only=True)
    fixed_scores.update({"AAA/USDT": 70})
    for n in range(300, 306):
        clock["n"] = n
        br.update()
    br.by_symbol["AAA/USDT"].engine.position.equity_before *= 10        # pretend it is far below its cost
    fixed_scores.update({"AAA/USDT": 50, "BBB/USDT": 95})
    clock["n"] = 306
    br.update()
    assert not (store.read("bankroll_ledger").kind == "ROTATE").any()


# ---------------------------------------------------------------- setup
def test_bankroll_setup_validation(tmp_path):
    path = str(tmp_path / "i.json")
    with pytest.raises(instances.InstanceError, match="need sizing 'bankroll'"):
        instances.create({"name": "aa", "stakes": [1, 2, 3, 4]}, path)
    with pytest.raises(instances.InstanceError, match="lower-timeframe score input"):
        instances.create({"name": "bb", "sizing": "bankroll", "timeframe": "1h", "score_inputs": ["lower_tf"]}, path)
    with pytest.raises(instances.InstanceError, match="must not get smaller"):
        instances.create({"name": "cc", "sizing": "bankroll", "stakes": [5, 3, 5, 8]}, path)
    with pytest.raises(instances.InstanceError, match="rotation options need rotation"):
        instances.create({"name": "dd", "sizing": "bankroll", "rotate_trim": True}, path)
    with pytest.raises(instances.InstanceError, match="maximum entry"):
        instances.create({"name": "ee", "sizing": "bankroll", "min_entry": 50, "max_entry": 20}, path)
    with pytest.raises(instances.InstanceError, match="only paper"):
        instances.create({"name": "ff", "mode": "testnet", "sizing": "bankroll"}, path)
    inst = instances.create({"name": "gg", "sizing": "bankroll", "equity": 5000}, path)
    assert inst.stakes == [0, 3, 5, 8] and inst.min_entry == 10 and inst.score_inputs == list(bankroll.DEFAULT_INPUTS)
    assert inst.engine_config().initial_equity == 0
    with pytest.raises(instances.InstanceError, match="can't be changed"):
        instances.update("gg", path, stakes=[1, 1, 1, 1])
    assert instances.update("gg", path, low_basis="free", low_pct=25).low_pct == 25
    old = instances.create({"name": "hh"}, path)              # other sizings: nothing changes
    assert old.stakes is None and old.engine_config().initial_equity == 1000
