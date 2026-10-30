import numpy as np
import pandas as pd
import pytest

from kripto_bot import instances
from kripto_bot.core import lower_timeframes as ltf
from kripto_bot.core import strategy
from kripto_bot.core.engine import Engine, EngineConfig, Position, run_backtest
from kripto_bot.core.indicators import add_indicators
from kripto_bot.trading import position_watch, run_realtime
from kripto_bot.trading.store import PaperStore

H = pd.Timedelta(hours=1)
D = pd.Timedelta(days=1)
START = pd.Timestamp("2025-01-01")
DAYS = 375
FIRST_DAY = 330                    # the bots start here; the days before are history


def _agg(h, rule):
    g = h.set_index("timestamp").resample(rule)
    return pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(),
                         "close": g.close.last(), "volume": g.volume.sum()}).reset_index()


def _hours(seed):
    rng = np.random.default_rng(seed)
    n = DAYS * 24
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.012, n)))
    open_ = np.r_[100, close[:-1]]
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, n)))
    return pd.DataFrame({"timestamp": pd.date_range(START, periods=n, freq="1h"),
                         "open": open_, "high": high, "low": low, "close": close, "volume": 1.0})


class Market:
    """Synthetic 1h random walk with its 4h, 1d and (one per hour) 1m candles; serves
    only the candles closed by `now`, like Binance."""

    def __init__(self, seed=3):
        h = _hours(seed)
        m = h[["timestamp", "open"]].assign(high=h.open, low=h.open, close=h.open, volume=1.0)
        self.frames = {"1h": h, "4h": _agg(h, "4h"), "1d": _agg(h, "1D"), "1m": m}
        self.now = START + FIRST_DAY * D + pd.Timedelta(minutes=2)

    def closed(self, tf):
        f = self.frames[tf]
        step = pd.Timedelta(milliseconds=run_realtime.timeframe_ms(tf))
        return f[f.timestamp + step <= self.now]

    def candles(self, symbol, tf, since):
        c = self.closed(tf)
        return c[c.timestamp >= since].reset_index(drop=True)

    def recent(self, symbol, tf, limit=200, **kw):
        return self.closed(tf).tail(limit).reset_index(drop=True)


@pytest.fixture
def market(monkeypatch):
    m = Market()
    monkeypatch.setattr(run_realtime, "fetch_recent", m.recent)
    monkeypatch.setattr(run_realtime, "now_ms", lambda: int(m.now.value // 10**6))
    monkeypatch.setattr(ltf, "closed_candles", m.candles)
    return m


def _runner(store, entry=False, exit=False, stop=None):
    cfg = EngineConfig(atr_multiplier=stop)
    return run_realtime.SymbolRunner("X/USDT", "1d", "v9", cfg, store, params={"fast": 1, "slow": 50},
                                     label="run", lower_tf_entry=entry, lower_tf_exit=exit)


def _trades(runner):
    return [(t.entry_time, t.exit_time, round(t.exit_price, 8), t.reason) for t in runner.engine.trades]


def _simulate(market, tmp_path, name, every_days=1, live=False, live_fetch=None, **kw):
    """Run a bot from FIRST_DAY to the end: updates every `every_days` closes and, if live,
    the early-exit watcher two minutes after every hour."""
    store = PaperStore(str(tmp_path / f"{name}.db"))
    r = _runner(store, **kw)
    market.now = START + FIRST_DAY * D + pd.Timedelta(minutes=2)
    r.update()                                         # records the starting point
    watch = position_watch.EarlyExitWatch([r], store, "1d", fetch=live_fetch or market.candles,
                                          clock=lambda: market.now) if live else None
    live_exits = 0
    for hour in range(1, (DAYS - FIRST_DAY - 1) * 24 + 1):
        market.now = START + FIRST_DAY * D + hour * H + pd.Timedelta(minutes=2)
        if watch:
            n = len(r.engine.trades)
            watch.check()
            live_exits += len(r.engine.trades) - n
        if hour % (24 * every_days) == 0:
            r.update()
    market.now = START + (DAYS - 1) * D + pd.Timedelta(minutes=2)
    r.update()
    return r, store, live_exits


# ---------------------------------------------------------------- pieces
def test_signal_at_uses_the_last_closed_candle_only():
    frame = pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=6, freq="4h"),
                          "signal": ["-", "BUY", "SELL", "BUY", "SELL", "BUY"]}).set_index("timestamp", drop=False)
    at = pd.Timestamp("2026-01-01 13:00")              # the 12:00 candle (BUY) is still forming
    assert ltf.signal_at(frame, "4h", at) == "SELL"    # 08:00-12:00
    assert ltf.signal_at(frame, "4h", pd.Timestamp("2026-01-01 12:00")) == "SELL"
    assert ltf.signal_at(frame, "4h", pd.Timestamp("2026-01-01 11:59")) == "BUY"   # 04:00-08:00
    assert ltf.signal_at(frame, "4h", pd.Timestamp("2026-01-03")) is None   # missing candle: no decision


def _frames(sig4, sig1, lows=None, day="2026-01-02"):
    d = pd.Timestamp(day)
    f4 = pd.DataFrame({"timestamp": pd.date_range(d - 4 * pd.Timedelta(hours=4), periods=10, freq="4h"), "signal": sig4})
    f1 = pd.DataFrame({"timestamp": pd.date_range(d - H, periods=25, freq="1h"), "signal": sig1,
                       "open": 100.0, "low": lows if lows is not None else 99.0})
    return {"4h": f4.set_index("timestamp", drop=False), "1h": f1.set_index("timestamp", drop=False)}, d


def test_early_exit_time_needs_both_timeframes():
    sig4 = ["BUY"] * 4 + ["BUY", "SELL", "SELL", "SELL", "SELL", "SELL"]   # 4h SELL from 04:00-08:00 on
    sig1 = ["BUY"] * 6 + ["SELL"] * 19                                     # 1h SELL from the 04:00 candle on
    frames, day = _frames(sig4, sig1)
    # 08:00: last 4h candle (04-08) SELL, last 1h candle (07-08) SELL
    assert ltf.early_exit_time(frames, day, "1d") == day + 8 * H


def test_early_exit_time_is_strictly_inside_the_candle():
    frames, day = _frames(["BUY"] * 10, ["BUY"] * 25)
    assert ltf.early_exit_time(frames, day, "1d") is None
    frames, day = _frames(["SELL"] * 10, ["SELL"] * 25)
    assert ltf.early_exit_time(frames, day, "1d") == day + H            # first finest close


def test_the_stop_reached_first_wins():
    lows = [99.0] * 3 + [90.0] + [99.0] * 21          # the 02:00-03:00 candle reaches the stop
    sig4 = ["BUY"] * 5 + ["SELL"] * 5
    frames, day = _frames(sig4, ["SELL"] * 25, lows)
    assert ltf.early_exit_time(frames, day, "1d", stop=95.0) is None
    assert ltf.early_exit_time(frames, day, "1d", stop=80.0) == day + 8 * H   # 4h SELL from the 04:00 candle


def test_engine_entry_ok_and_exit_inside():
    e = Engine(EngineConfig(atr_multiplier=None, slippage_bps=0, fee_rate=0))
    e.on_bar(START, 100, 101, 99, 100, "BUY", entry_ok=False)
    assert e.pending is None
    e.on_bar(START + D, 100, 101, 99, 100, "BUY")
    assert e.pending[0] == "ENTER"
    trade = e.exit_inside(START + 2 * D, 102, START + 2 * D + 5 * H, 104)   # fills the entry at 102 first
    assert (trade.entry_price, trade.exit_price, trade.reason) == (102, 104, "LTF")
    assert e.position is None and e.pending is None and e.reentry_above == 102   # no close yet: entry price
    e.on_bar(START + 2 * D, 102, 105, 101, 103, "BUY")
    assert e.pending[0] == "ENTER"                     # closed above the re-entry level (102)
    e2 = Engine(EngineConfig(atr_multiplier=None))
    assert e2.exit_inside(START, 100, START + H, 100) is None


def test_reentry_after_an_early_exit_waits_for_a_reset():
    e = Engine(EngineConfig(atr_multiplier=None, slippage_bps=0, fee_rate=0))
    e.position = Position("t", "LONG", 1.0, START, 100.0, 0.0, 100.0, None, high=110.0)
    e.exit_inside(START + D, 105, START + D + 3 * H, 104)
    assert e.reentry_above == 110.0
    e.on_bar(START + D, 105, 106, 103, 104, "BUY")
    assert e.pending is None                           # BUY but below the trade's highest close
    e.on_bar(START + 2 * D, 104, 112, 103, 111, "BUY")
    assert e.pending[0] == "ENTER"                     # a close above it


# ---------------------------------------------------------------- the bot
def test_live_watcher_and_catch_up_give_the_same_trades(market, tmp_path):
    live, live_store, live_exits = _simulate(market, tmp_path, "live", live=True, exit=True)
    daily, _, _ = _simulate(market, tmp_path, "daily", exit=True)
    late, _, _ = _simulate(market, tmp_path, "late", every_days=5, exit=True)
    assert live_exits > 0                              # the watcher sold during the candle
    assert _trades(live) == _trades(daily) == _trades(late)
    reasons = [t.reason for t in live.engine.trades]
    assert "LTF" in reasons
    for t in live.engine.trades:
        if t.reason == "LTF":
            assert t.exit_time.hour != 0               # inside the day, at an hourly open
            fine = market.frames["1h"]
            assert t.exit_price == pytest.approx(fine.loc[fine.timestamp == t.exit_time, "open"].iloc[0] * (1 - 5e-4))
    logged = live_store.read("signals")
    assert logged.action.str.startswith("lower-timeframe exit").sum() == reasons.count("LTF")


def test_without_the_settings_nothing_changes(market, tmp_path):
    off, _, _ = _simulate(market, tmp_path, "off")
    df = add_indicators(market.frames["1d"].iloc[:DAYS - 1].copy())
    sig = strategy.get_strategy("v9")(df, fast=1, slow=50)
    expected, _ = run_backtest(df.iloc[FIRST_DAY:], sig.iloc[FIRST_DAY:], EngineConfig(atr_multiplier=None))
    expected = expected[expected.reason != "END"]
    assert [t.pnl for t in off.engine.trades] == pytest.approx(expected.pnl.tolist())


def test_entry_confirmation(market, tmp_path):
    plain, _, _ = _simulate(market, tmp_path, "plain")
    confirmed, store, _ = _simulate(market, tmp_path, "confirmed", entry=True)
    v9 = lambda d: strategy.get_strategy("v9")(d, fast=1, slow=50)
    frames = {tf: ltf.with_signals(market.frames[tf].copy(), v9) for tf in ("4h", "1h")}
    for t in confirmed.engine.trades:
        assert ltf.all_say(frames, t.entry_time, "BUY")     # decided at the previous daily close
    assert _trades(confirmed) != _trades(plain)
    actions = store.read("signals").action
    assert (actions == "ignored (lower timeframes don't confirm)").any()


@pytest.mark.parametrize("entry,exit", [(True, False), (False, True), (True, True)])
def test_backtest_makes_the_paper_bots_trades(market, tmp_path, entry, exit):
    """lower_timeframes.run_backtest (the archive test of E and X) takes the same
    decisions as the paper bot, so the test measures what the bot does."""
    paper, _, _ = _simulate(market, tmp_path, f"paper-{entry}-{exit}", entry=entry, exit=exit)
    v9 = lambda d: strategy.get_strategy("v9")(d, fast=1, slow=50)
    daily = add_indicators(market.frames["1d"].iloc[:DAYS - 1].copy())
    frames = {tf: ltf.with_signals(market.frames[tf].copy(), v9) for tf in ("4h", "1h")}
    sig = v9(daily)
    trades, equity = ltf.run_backtest(daily.iloc[FIRST_DAY:], sig.iloc[FIRST_DAY:], frames,
                                      EngineConfig(atr_multiplier=None), entry=entry, exit=exit)
    closed = trades[trades.reason != "END"]
    assert list(zip(closed.entry_time, closed.exit_time, closed.exit_price.round(8), closed.reason)) == _trades(paper)
    assert len(equity) == DAYS - 1 - FIRST_DAY


def test_backtest_without_rules_is_the_engines(market):
    v9 = lambda d: strategy.get_strategy("v9")(d, fast=1, slow=50)
    daily = add_indicators(market.frames["1d"].copy())
    frames = {tf: ltf.with_signals(market.frames[tf].copy(), v9) for tf in ("4h", "1h")}
    plain, eq1 = run_backtest(daily, v9(daily), EngineConfig(atr_multiplier=None))
    same, eq2 = ltf.run_backtest(daily, v9(daily), frames, EngineConfig(atr_multiplier=None))
    assert plain.drop(columns="trade_id").equals(same.drop(columns="trade_id")) and eq1.equals(eq2)


def test_single_timeframe_exit_decides_at_its_own_closes():
    frames, day = _frames(["BUY"] * 4 + ["BUY", "SELL", "SELL", "SELL", "SELL", "SELL"], ["BUY"] * 25)
    only4h = {"4h": frames["4h"].assign(low=99.0, open=100.0)}
    assert ltf.early_exit_time(only4h, day, "1d") == day + 8 * H      # the 04:00-08:00 candle says SELL
    assert ltf.early_exit_time(frames, day, "1d") is None             # 1h never agrees


def test_watcher_never_guesses_the_fill(market, tmp_path):
    """Without the 1m candle at the exit time the watcher doesn't sell; the main close
    then applies the rule with the finest candle's open, so the trades are the same."""
    no_minutes = lambda s, tf, since: market.candles(s, tf, since) if tf != "1m" else market.frames["1m"].iloc[0:0]
    blind, _, live_exits = _simulate(market, tmp_path, "blind", live=True, live_fetch=no_minutes, exit=True)
    daily, _, _ = _simulate(market, tmp_path, "daily", exit=True)
    assert live_exits == 0 and _trades(blind) == _trades(daily)


# ---------------------------------------------------------------- settings
def test_settings_are_validated(tmp_path):
    path = str(tmp_path / "i.json")
    inst = instances.create({"name": "e-x", "timeframe": "4h", "lower_tf_entry": True, "lower_tf_exit": "true"}, path)
    assert inst.lower_tf_entry is True and inst.lower_tf_exit is True
    with pytest.raises(instances.InstanceError, match="4h"):
        instances.create({"name": "fast", "timeframe": "1h", "lower_tf_exit": True}, path)
    with pytest.raises(instances.InstanceError, match="paper"):
        instances.create({"name": "tn", "mode": "testnet", "lower_tf_entry": True}, path)
    with pytest.raises(instances.InstanceError, match="true or false"):
        instances.create({"name": "bad", "lower_tf_entry": 1}, path)
    with pytest.raises(instances.InstanceError, match="can't be changed"):
        instances.update("e-x", path, lower_tf_exit=False)
    assert instances.create({"name": "plain"}, path).lower_tf_entry is False


def test_watches_for_an_instance(tmp_path):
    store = PaperStore(str(tmp_path / "p.db"))
    assert position_watch.for_instance(instances.Instance(name="a", stop="none"), [], store) is None
    w = position_watch.for_instance(instances.Instance(name="b", stop="tight", lower_tf_exit=True), [], store)
    assert [type(x).__name__ for x in w.watches] == ["StopWatch", "EarlyExitWatch"]
