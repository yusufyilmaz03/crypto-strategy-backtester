# playground.py
"""Interactive backtests for the dashboard's playground.

A run backtests one symbol with any strategy, parameters and stop on cached Binance
data, using the same engine as everything else, and returns what the page needs
to draw it: candles, trade markers, indicator overlays, equity and buy-and-hold.
Each run is logged in playground.db: every look at the data is a decision, and the
count is a reminder of how many variants have been tried.

Unused holdout periods (config.UNUSED_HOLDOUTS) are cut off unless include_holdout
is set, so playing around doesn't spend them by accident.
"""
import json
import os
import sqlite3
from dataclasses import replace

import pandas as pd

import config
import instances
import strategy
from data import load_ohlcv, timeframe_ms
from engine import run_backtest
from indicators import add_indicators
from metrics import compute_metrics

HERE = os.path.dirname(os.path.abspath(__file__))
PLAYGROUND_DB = os.path.join(HERE, "playground.db")
MAX_CANDLES = 20_000
WARMUP = 300           # extra candles before the window so indicators are ready


class PlaygroundError(ValueError):
    pass


def _overlays(key, df, params):
    """Indicator lines that explain the strategy's decisions."""
    p = {x["name"]: x["default"] for x in instances.param_spec(key)}
    p.update(params)
    close, out = df["close"], {}
    if key == "v9":
        if p["fast"] > 1:
            out[f"SMA {p['fast']}"] = close.rolling(p["fast"]).mean()
        out[f"SMA {p['slow']}"] = close.rolling(p["slow"]).mean()
    elif key in ("v1", "v2", "v3", "v5"):
        for n in (p["fast"], p["slow"]):
            out[f"EMA {n}"] = close.ewm(span=n, adjust=False).mean()
    elif key == "v7":
        for n in (p["trend_fast"], p["trend_slow"]):
            out[f"EMA {n}"] = close.ewm(span=n, adjust=False).mean()
    elif key == "v4":
        ma = close.rolling(p["bb_len"]).mean()
        sd = close.rolling(p["bb_len"]).std(ddof=0)
        out["BB upper"], out["BB mid"], out["BB lower"] = ma + p["bb_mult"] * sd, ma, ma - p["bb_mult"] * sd
    elif key == "v6":
        out["Donchian high"] = df["high"].rolling(p["length"]).max().shift(1)
        out["Donchian low"] = df["low"].rolling(p["length"]).min().shift(1)
    elif key == "v10":
        out["Entry channel"] = df["high"].rolling(p["entry"]).max().shift(1)
        out["Exit channel"] = df["low"].rolling(p["exit_len"]).min().shift(1)
    return out


def _json_num(v):
    """NaN and infinity aren't valid JSON: NaN -> None, inf -> a large sentinel string."""
    if isinstance(v, float):
        if v != v:
            return None
        if v in (float("inf"), float("-inf")):
            return "inf" if v > 0 else "-inf"
        return float(v)
    return v


def _secs(ts):
    return [int(t.timestamp()) for t in pd.to_datetime(ts)]


def run(symbol, timeframe, strategy_key, params=None, stop="none", days=365, include_holdout=False,
        log=True, db=None, loader=None):
    symbol = symbol.strip().upper()
    if strategy_key not in strategy.STRATEGY_DISPATCH:
        raise PlaygroundError(f"unknown strategy {strategy_key}")
    if timeframe not in instances.TIMEFRAMES:
        raise PlaygroundError(f"timeframe must be one of {', '.join(instances.TIMEFRAMES)}")
    if stop not in config.STOPLOSS_CONFIG:
        raise PlaygroundError(f"unknown stop {stop}")
    try:
        params = instances._clean_params(strategy_key, params or {})
    except instances.InstanceError as e:
        raise PlaygroundError(str(e))
    days = float(days)
    candles = days * 86_400_000 / timeframe_ms(timeframe)
    if not 10 <= candles <= MAX_CANDLES:
        raise PlaygroundError(f"{days:g} days of {timeframe} is {candles:.0f} candles; allowed 10-{MAX_CANDLES}")

    warm_days = WARMUP * timeframe_ms(timeframe) / 86_400_000
    df = (loader or load_ohlcv)(symbol, timeframe, days + warm_days)
    if df.empty:
        raise PlaygroundError(f"no data for {symbol} {timeframe}")
    note = None
    cutoff = config.UNUSED_HOLDOUTS.get(timeframe)
    if cutoff and not include_holdout:
        cut = pd.Timestamp(cutoff)
        if df["timestamp"].iloc[-1] >= cut:
            df = df[df["timestamp"] < cut].reset_index(drop=True)
            note = f"Data after {cut:%Y-%m-%d} is an unused {timeframe} holdout and was left out."
    df = add_indicators(df)
    start = df["timestamp"].iloc[-1] - pd.Timedelta(days=days)
    first = int((df["timestamp"] <= start).sum())
    if len(df) - first < 10:
        raise PlaygroundError("not enough candles in the selected window")

    signals = strategy.get_strategy(strategy_key)(df, **params)
    cfg = replace(config.engine_config(), atr_multiplier=config.STOPLOSS_CONFIG[stop])
    win = df.iloc[first:]
    tr, eq = run_backtest(win, signals.iloc[first:], cfg, symbol)
    m = compute_metrics(tr, eq, timeframe, cfg.initial_equity, win["close"])

    t = _secs(win["timestamp"])
    result = {
        "symbol": symbol, "timeframe": timeframe, "strategy": strategy_key, "params": params, "stop": stop,
        "start": str(win["timestamp"].iloc[0]), "end": str(win["timestamp"].iloc[-1]), "note": note,
        "metrics": {k: _json_num(v) for k, v in m.items()},
        "candles": [{"time": a, "open": o, "high": h, "low": lo, "close": c}
                    for a, o, h, lo, c in zip(t, win["open"], win["high"], win["low"], win["close"])],
        "equity": [{"time": a, "value": round((v / cfg.initial_equity - 1) * 100, 4)} for a, v in zip(t, eq.values)],
        "buy_hold": [{"time": a, "value": round((c / win["close"].iloc[0] - 1) * 100, 4)}
                     for a, c in zip(t, win["close"])],
        "overlays": {name: [{"time": a, "value": float(v)} for a, v in zip(t, s.iloc[first:]) if v == v]
                     for name, s in _overlays(strategy_key, df, params).items()},
        "trades": [{"entry_time": int(pd.Timestamp(r.entry_time).timestamp()),
                    "exit_time": int(pd.Timestamp(r.exit_time).timestamp()),
                    "entry_price": r.entry_price, "exit_price": r.exit_price,
                    "return_pct": r.return_pct, "reason": r.reason} for r in tr.itertuples()],
    }
    if log:
        result["run_id"] = _log(db or PLAYGROUND_DB, result)
    return result


def _connect(db):
    conn = sqlite3.connect(db, timeout=10)
    conn.execute("""CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, symbol TEXT, timeframe TEXT,
        strategy TEXT, params TEXT, stop TEXT, start TEXT, end TEXT, include_holdout INTEGER,
        return_pct REAL, buy_hold_pct REAL, sharpe REAL, max_drawdown_pct REAL, trades INTEGER)""")
    return conn


def _log(db, r):
    m = r["metrics"]
    with _connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO runs (created_at, symbol, timeframe, strategy, params, stop, start, end, include_holdout,"
            " return_pct, buy_hold_pct, sharpe, max_drawdown_pct, trades) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pd.Timestamp.now(tz="UTC").isoformat(), r["symbol"], r["timeframe"], r["strategy"],
             json.dumps(r["params"]), r["stop"], r["start"], r["end"], int(r["note"] is None),
             m["return_pct"], m["buy_hold_pct"], m["sharpe"], m["max_drawdown_pct"], m["trades"]))
        return cur.lastrowid


def history(limit=50, db=None):
    with _connect(db or PLAYGROUND_DB) as conn:
        total = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        rows = pd.read_sql_query("SELECT * FROM runs ORDER BY id DESC LIMIT ?", conn, params=(limit,))
    return total, rows
