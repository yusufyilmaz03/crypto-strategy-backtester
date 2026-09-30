import pandas as pd
import pytest

import broker as broker_mod
import config
from broker import FakeBroker, client_order_id
from conftest import make_ohlcv
from engine import Engine, EngineConfig
from run_testnet import RiskGuard, SymbolTrader, Trader, decide
from store import TradingStore

SYM = "BTC/USDT"


def candles(n, close=100.0, start="2026-01-01"):
    df = make_ohlcv(n, seed=1, start=start, freq="1D")
    df["close"] = close
    return df


class Sig:
    """Signal function returning a fixed signal for the last candle."""

    def __init__(self, value="-"):
        self.value = value

    def __call__(self, df):
        s = pd.Series("-", index=df.index)
        s.iloc[-1] = self.value
        return s


@pytest.fixture
def setup(tmp_path):
    store = TradingStore(str(tmp_path / "t.db"))
    brk = FakeBroker({SYM: 100.0}, {"USDT": 10_000.0, "BTC": 0.0})
    sig = Sig()
    trader = SymbolTrader(SYM, brk, store, sig, "1d", allocation=500.0)
    return store, brk, sig, trader


def test_decide_matches_engine_rules():
    for in_pos in (False, True):
        for signal in ("BUY", "SELL", "-"):
            eng = Engine(EngineConfig())
            if in_pos:
                eng.on_bar(pd.Timestamp("2026-01-01"), 1, 1, 1, 1, "BUY", None)
                eng.on_bar(pd.Timestamp("2026-01-02"), 1, 1, 1, 1, "-", None)
            eng.on_bar(pd.Timestamp("2026-01-03"), 1, 1, 1, 1, signal, None)
            expected = None if eng.pending is None else ("buy" if eng.pending[0] == "ENTER" else "sell")
            assert decide(in_pos, signal) == expected, (in_pos, signal)


def test_buy_then_sell_accounting(setup):
    store, brk, sig, trader = setup
    sig.value = "BUY"
    trader.step(df=candles(60))
    pos = store.position(SYM)
    assert pos["qty"] == pytest.approx(round(100 / 100, 6) * 0.999)   # capped at MAX_ORDER_USDT=100, fee in base
    assert store.cash(SYM, 0) == pytest.approx(500 - 100)

    brk.prices[SYM] = 110.0
    sig.value = "SELL"
    trader.step(df=candles(61, close=110.0))
    assert store.position(SYM) is None
    orders = store.read("orders")
    sell = orders[orders.side == "sell"].iloc[0]
    proceeds = pos["qty"] * 110 * (1 - 0.001)
    assert sell.pnl == pytest.approx(proceeds - 100)
    assert store.cash(SYM, 0) == pytest.approx(400 + proceeds)
    assert sell.ref_price == 110.0


def test_same_candle_is_only_traded_once(setup):
    store, brk, sig, trader = setup
    sig.value = "BUY"
    df = candles(60)
    trader.step(df=df)
    trader.step(df=df)
    assert len(brk.sent) == 1


def test_order_sent_before_crash_is_not_sent_again(setup):
    store, brk, sig, trader = setup
    df = candles(60)
    t = df.timestamp.iloc[-1]
    brk.market_buy(SYM, 100.0, client_order_id(SYM, t, "buy"))   # sent, but never recorded
    sig.value = "BUY"
    trader.step(df=df)
    assert len(brk.sent) == 1 and store.position(SYM) is not None


def test_min_order_skip(setup):
    store, brk, sig, trader = setup
    trader.allocation = 3.0
    sig.value = "BUY"
    trader.step(df=candles(60))
    assert brk.sent == [] and store.position(SYM) is None
    assert "below the minimum" in store.read("events").message.iloc[-1]


def test_broker_error_is_recorded_and_state_unchanged(setup):
    store, brk, sig, trader = setup
    brk.fail_next = broker_mod.ccxt.InsufficientFunds("no money")
    sig.value = "BUY"
    trader.step(df=candles(60))
    assert store.position(SYM) is None
    assert store.read("orders").status.tolist() == ["error"]


def test_reconcile_shrinks_position_to_balance(setup):
    store, brk, sig, trader = setup
    store.set_position(SYM, 1.0, 100.0, 100.0, "2026-01-01", "x")
    brk.balances["BTC"] = 0.4
    trader.reconcile()
    assert store.position(SYM)["qty"] == pytest.approx(0.4)


def test_kill_switch_blocks_and_flattens(tmp_path, setup):
    store, brk, sig, trader = setup
    kill = tmp_path / "KILL"
    guard = RiskGuard(store, kill_file=str(kill), daily_loss_limit_pct=5)
    t = Trader(brk, store, symbols=[SYM], capital=500.0, guard=guard)
    t.traders[0].signal_fn = sig

    kill.write_text("")
    sig.value = "BUY"
    t.run_once(frames={SYM: candles(60)})
    assert brk.sent == []                          # entries blocked

    kill.unlink()
    t.run_once(frames={SYM: candles(61)})
    assert store.position(SYM) is not None

    kill.write_text("flatten")
    sig.value = "-"
    t.run_once(frames={SYM: candles(62)})
    assert store.position(SYM) is None


def test_daily_loss_limit(setup):
    store, brk, sig, trader = setup
    guard = RiskGuard(store, kill_file="/nonexistent", daily_loss_limit_pct=5)
    assert guard.entries_allowed(1000.0, "2026-01-01")
    assert guard.entries_allowed(960.0, "2026-01-01")
    assert not guard.entries_allowed(940.0, "2026-01-01")
    assert not guard.entries_allowed(1000.0, "2026-01-01")   # stays blocked for the day
    assert guard.entries_allowed(940.0, "2026-01-02")         # new day, new start


def test_live_mode_is_locked(monkeypatch):
    monkeypatch.setattr(config, "LIVE_TRADING_ENABLED", False)
    with pytest.raises(broker_mod.LiveTradingDisabled):
        broker_mod.CcxtBroker(mode="live", api_key="k", secret="s")


def test_missing_testnet_keys(monkeypatch):
    monkeypatch.setattr(config, "BINANCE_TESTNET_API_KEY", "")
    with pytest.raises(RuntimeError):
        broker_mod.CcxtBroker(mode="testnet")


def test_client_order_id_format():
    cid = client_order_id("PEPE/USDT", pd.Timestamp("2026-10-01"), "buy")
    assert len(cid) <= 36 and cid.endswith("-b")
    assert cid == client_order_id("PEPE/USDT", pd.Timestamp("2026-10-01"), "buy")


def test_startup_equity_includes_open_positions(setup):
    """Before any price is seen, equity must not drop the position (false loss-limit trigger)."""
    store, brk, sig, trader = setup
    sig.value = "BUY"
    trader.step(df=candles(60))
    before = trader.equity()
    fresh = SymbolTrader(SYM, brk, store, sig, "1d", allocation=500.0)   # restart
    assert fresh.last_close is None
    assert fresh.equity() == pytest.approx(before)
