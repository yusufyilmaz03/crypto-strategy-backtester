import pandas as pd
import pytest

import run_realtime
import strategy
from conftest import make_ohlcv
from engine import EngineConfig, run_backtest
from indicators import add_indicators


def test_paper_loop_matches_backtest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    full = make_ohlcv(900, seed=11)
    visible = {"n": 400}

    def fake_fetch_recent(symbol, timeframe, limit=200, **kw):
        # Only candles that have "closed" so far, last `limit` of them.
        return full.iloc[:visible["n"]].tail(limit).reset_index(drop=True)

    monkeypatch.setattr(run_realtime, "fetch_recent", fake_fetch_recent)
    cfg = EngineConfig()
    runner = run_realtime.SymbolRunner("X/USDT", "5m", "v5", cfg)

    runner.update()                      # first call only records the starting point
    assert runner.engine.trades == [] and runner.engine.position is None
    start = visible["n"]
    while visible["n"] < len(full):
        visible["n"] += 3                # sometimes several candles close between updates
        runner.update()

    # Backtest over the same candles, with signals computed on the full history.
    df = add_indicators(full.copy())
    sig = strategy.get_strategy("v5")(df)
    trades, _ = run_backtest(df.iloc[start:], sig.iloc[start:], cfg)
    closed = trades[trades.reason != "END"]

    assert len(closed) > 0
    assert [t.pnl for t in runner.engine.trades] == pytest.approx(closed.pnl.tolist())

    log = pd.read_csv(tmp_path / "trades_log.csv")
    done = log[log["Close Reason"].notna()]
    assert done["TradeID"].tolist() == [t.trade_id for t in runner.engine.trades]
    assert done["PnL"].tolist() == pytest.approx([t.pnl for t in runner.engine.trades], abs=1e-6)
    assert log["Close Reason"].isna().sum() == (1 if runner.engine.position else 0)

    signals = pd.read_csv(tmp_path / "signals_log.csv")
    assert set(signals["Signal"]) <= {"BUY", "SELL"}
    assert signals["Action"].str.len().gt(0).all()


def test_old_log_format_is_moved_aside(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "signals_log.csv").write_text("Timestamp,Symbol,Signal\n2025-01-01,X,BUY\n")
    run_realtime.log_signal("X/USDT", pd.Timestamp("2026-01-01"), "BUY", 30.0, 1.0, None, "enter LONG at next open")
    log = pd.read_csv(tmp_path / "signals_log.csv")
    assert list(log.columns) == run_realtime.SIGNAL_COLUMNS and len(log) == 1
    assert len(list(tmp_path.glob("signals_log.csv.*.bak"))) == 1
