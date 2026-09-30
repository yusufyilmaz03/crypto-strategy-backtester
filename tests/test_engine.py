import pandas as pd
import pytest

from engine import Engine, EngineConfig, run_backtest

NO_COSTS = EngineConfig(fee_rate=0.0, slippage_bps=0.0, atr_multiplier=2.0, initial_equity=1000.0)


def frame(bars, signals, atr=5.0):
    """bars: list of (open, high, low, close)."""
    df = pd.DataFrame(bars, columns=["open", "high", "low", "close"])
    df["timestamp"] = pd.date_range("2025-01-01", periods=len(df), freq="1h")
    df["ATR"] = atr
    df["RSI"] = 50.0
    return df, pd.Series(signals)


def test_entry_fills_at_next_open_not_signal_close():
    df, sig = frame([(100, 101, 99, 100), (102, 103, 101, 102), (104, 105, 103, 104)],
                    ["BUY", "-", "-"])
    trades, equity = run_backtest(df, sig, NO_COSTS)
    t = trades.iloc[0]
    assert t.entry_price == 102                     # open of the candle after the signal
    assert t.entry_time == df.timestamp[1]
    assert t.reason == "END" and t.exit_price == 104
    assert equity.iloc[0] == 1000                   # flat on the signal candle
    assert equity.iloc[-1] == pytest.approx(1000 * 104 / 102)


def test_exit_signal_fills_at_next_open():
    df, sig = frame([(100, 100, 100, 100), (100, 100, 100, 100), (110, 110, 110, 110),
                     (120, 120, 120, 120)], ["BUY", "SELL", "-", "-"])
    trades, _ = run_backtest(df, sig, NO_COSTS)
    assert len(trades) == 1
    assert trades.iloc[0].exit_price == 110 and trades.iloc[0].reason == "SIGNAL"
    assert trades.iloc[0].return_pct == pytest.approx(10.0)


def test_stop_hit_inside_candle_fills_at_stop():
    # entry 100, ATR 5 x 2 -> stop 90; next candle trades down to 89
    df, sig = frame([(100, 100, 100, 100), (100, 101, 99, 100), (95, 96, 89, 92)],
                    ["BUY", "-", "-"])
    trades, _ = run_backtest(df, sig, NO_COSTS)
    t = trades.iloc[0]
    assert t.reason == "STOP" and t.exit_price == 90
    assert t.exit_time == df.timestamp[2]


def test_gap_through_stop_fills_at_open():
    df, sig = frame([(100, 100, 100, 100), (100, 101, 99, 100), (85, 86, 80, 84)],
                    ["BUY", "-", "-"])
    trades, _ = run_backtest(df, sig, NO_COSTS)
    assert trades.iloc[0].exit_price == 85


def test_stop_can_trigger_on_entry_candle():
    df, sig = frame([(100, 100, 100, 100), (100, 100, 88, 95)], ["BUY", "-"])
    trades, _ = run_backtest(df, sig, NO_COSTS)
    assert trades.iloc[0].reason == "STOP" and trades.iloc[0].exit_price == 90


def test_missing_atr_means_no_stop():
    df, sig = frame([(100, 100, 100, 100), (100, 100, 50, 60)], ["BUY", "-"], atr=float("nan"))
    trades, _ = run_backtest(df, sig, NO_COSTS)
    assert trades.iloc[0].reason == "END"


def test_long_only_ignores_sell_when_flat():
    df, sig = frame([(100, 100, 100, 100), (100, 100, 100, 100)], ["SELL", "-"])
    trades, _ = run_backtest(df, sig, NO_COSTS)
    assert trades.empty


def test_short_when_enabled():
    cfg = EngineConfig(fee_rate=0.0, slippage_bps=0.0, allow_short=True)
    df, sig = frame([(100, 100, 100, 100), (100, 100, 100, 100), (90, 90, 90, 90)],
                    ["SELL", "BUY", "-"])
    trades, _ = run_backtest(df, sig, cfg)
    t = trades.iloc[0]
    assert t.side == "SHORT" and t.exit_price == 90
    assert t.return_pct == pytest.approx(10.0)


def test_opposite_signal_closes_without_reversing():
    cfg = EngineConfig(fee_rate=0.0, slippage_bps=0.0, allow_short=True)
    df, sig = frame([(100,) * 4, (100,) * 4, (105,) * 4, (105,) * 4], ["BUY", "SELL", "-", "-"])
    trades, _ = run_backtest(df, sig, cfg)
    assert len(trades) == 1 and trades.iloc[0].side == "LONG"


def test_fees_and_slippage():
    cfg = EngineConfig(fee_rate=0.001, slippage_bps=10.0, initial_equity=1000.0)
    df, sig = frame([(100,) * 4, (100,) * 4, (110,) * 4, (110,) * 4], ["BUY", "SELL", "-", "-"])
    trades, equity = run_backtest(df, sig, cfg)
    t = trades.iloc[0]
    entry, exit_ = 100 * 1.001, 110 * 0.999
    qty = 1000 / (entry * 1.001)
    assert t.entry_price == pytest.approx(entry) and t.exit_price == pytest.approx(exit_)
    assert t.pnl == pytest.approx(qty * exit_ * 0.999 - 1000)
    assert equity.iloc[-1] == pytest.approx(1000 + t.pnl)


def test_compounding_across_trades():
    df, sig = frame([(100,) * 4, (100,) * 4, (110,) * 4, (110,) * 4, (121,) * 4, (121,) * 4],
                    ["BUY", "SELL", "BUY", "SELL", "-", "-"])
    trades, equity = run_backtest(df, sig, NO_COSTS)
    assert trades.return_pct.tolist() == pytest.approx([10.0, 10.0])
    assert equity.iloc[-1] == pytest.approx(1210.0)


def test_incremental_feed_matches_batch():
    """The paper trader feeds candles one at a time; results must match the batch run."""
    from conftest import make_ohlcv
    from indicators import add_indicators
    import strategy

    df = add_indicators(make_ohlcv(600, seed=7))
    sig = strategy.get_strategy("v5")(df)
    cfg = EngineConfig()
    trades, _ = run_backtest(df, sig, cfg)

    eng = Engine(cfg)
    for i in range(len(df)):
        # recompute signals on the history available at candle i only
        s_i = strategy.get_strategy("v5")(df.iloc[:i + 1]).iloc[-1]
        r = df.iloc[i]
        eng.on_bar(r.timestamp, r.open, r.high, r.low, r.close, s_i, r.ATR, r.RSI)
    eng.finish(df.timestamp.iloc[-1], df.close.iloc[-1])

    assert len(trades) > 0
    assert [t.pnl for t in eng.trades] == pytest.approx(trades.pnl.tolist())


def test_state_roundtrip_continues_identically():
    """Saving and restoring the engine mid-run must not change the results."""
    import json

    from conftest import make_ohlcv
    from indicators import add_indicators
    import strategy

    df = add_indicators(make_ohlcv(600, seed=13))
    sig = strategy.get_strategy("v5")(df)
    rows = list(zip(df.timestamp, df.open, df.high, df.low, df.close, sig, df.ATR, df.RSI))

    ref = Engine(EngineConfig())
    for r in rows:
        ref.on_bar(*r)

    a = Engine(EngineConfig())
    for r in rows[:300]:
        a.on_bar(*r)
    assert a.position is not None or a.pending is not None or a.trades  # something to restore
    b = Engine(EngineConfig())
    b.load_state(json.loads(json.dumps(a.to_state())))
    for r in rows[300:]:
        b.on_bar(*r)

    assert b.equity == pytest.approx(ref.equity)
    assert [t.pnl for t in a.trades + b.trades] == pytest.approx([t.pnl for t in ref.trades])


def test_no_stop_when_multiplier_is_none():
    cfg = EngineConfig(fee_rate=0.0, slippage_bps=0.0, atr_multiplier=None)
    df, sig = frame([(100, 100, 100, 100), (100, 100, 50, 60)], ["BUY", "-"])
    trades, _ = run_backtest(df, sig, cfg)
    assert trades.iloc[0].reason == "END"
