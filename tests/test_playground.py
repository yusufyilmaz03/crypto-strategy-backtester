import json

import pandas as pd
import pytest

from kripto_bot import config
from kripto_bot.app import playground
from conftest import make_ohlcv
from kripto_bot.app.playground import PlaygroundError, history, run


def loader(n=800, freq="1D", start="2024-01-01"):
    df = make_ohlcv(n, seed=5, freq=freq, start=start)
    return lambda symbol, tf, days: df


def test_run_returns_chart_data_and_logs(tmp_path):
    db = str(tmp_path / "pg.db")
    r = run("btc/usdt", "1d", "v9", {"slow": 50}, "none", 365, db=db, loader=loader())
    assert r["symbol"] == "BTC/USDT" and len(r["candles"]) == 365
    assert len(r["equity"]) == len(r["buy_hold"]) == len(r["candles"])
    assert "SMA 50" in r["overlays"] and r["overlays"]["SMA 50"]
    assert r["metrics"]["trades"] == len(r["trades"])
    json.dumps(r)                                          # must be valid JSON
    total, rows = history(db=db)
    assert total == 1 and rows.iloc[0]["strategy"] == "v9"


def test_unused_holdout_is_cut_unless_requested(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "UNUSED_HOLDOUTS", {"4h": "2024-04-01"})
    ld = loader(1500, "4h", "2024-01-01")
    r = run("BTC/USDT", "4h", "v8", {}, "none", 30, db=str(tmp_path / "pg.db"), loader=ld)
    assert pd.Timestamp(r["end"]) < pd.Timestamp("2024-04-01") and r["note"]
    r = run("BTC/USDT", "4h", "v8", {}, "none", 30, include_holdout=True, log=False, loader=ld)
    assert pd.Timestamp(r["end"]) > pd.Timestamp("2024-04-01") and r["note"] is None


@pytest.mark.parametrize("args, message", [
    (("BTC/USDT", "1d", "v99", {}, "none", 100), "unknown strategy"),
    (("BTC/USDT", "2d", "v9", {}, "none", 100), "timeframe"),
    (("BTC/USDT", "1d", "v9", {"slow": "x"}, "none", 100), "not a number"),
    (("BTC/USDT", "5m", "v9", {}, "none", 365), "candles"),
])
def test_invalid_requests(args, message):
    with pytest.raises(PlaygroundError, match=message):
        run(*args, log=False, loader=loader())


def test_matches_engine_directly():
    from kripto_bot.core.engine import run_backtest
    from kripto_bot.core.indicators import add_indicators
    from kripto_bot.core import strategy

    r = run("X/USDT", "1d", "v10", {}, "none", 300, log=False, loader=loader())
    df = add_indicators(make_ohlcv(800, seed=5, freq="1D", start="2024-01-01"))
    first = int((df.timestamp <= df.timestamp.iloc[-1] - pd.Timedelta(days=300)).sum())
    tr, _ = run_backtest(df.iloc[first:], strategy.get_strategy("v10")(df).iloc[first:], config.engine_config()
                         .__class__(**{**config.engine_config().__dict__, "atr_multiplier": None}))
    assert [t["return_pct"] for t in r["trades"]] == pytest.approx(tr.return_pct.tolist())
