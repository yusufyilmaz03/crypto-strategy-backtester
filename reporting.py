# reporting.py
"""Read-side helpers for the dashboard: results of an instance from its database."""
import json
import math
import os
import sqlite3

import pandas as pd

import config
from metrics import max_drawdown_pct
from store import DB_FILE

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")
STATUS_FILE = os.path.join(HERE, "status.json")


def _query(db, sql, params=()):
    if not os.path.exists(db):
        return pd.DataFrame()
    with sqlite3.connect(db, timeout=10) as conn:
        try:
            return pd.read_sql_query(sql, conn, params=params)
        except Exception:
            return pd.DataFrame()


def _db(inst, paper_db=DB_FILE):
    return paper_db if inst.mode == "paper" else inst.run_key


def equity_frame(inst, paper_db=DB_FILE):
    """Long frame: time, symbol, equity, close."""
    if inst.mode == "paper":
        df = _query(paper_db, "SELECT time, symbol, equity, close FROM equity WHERE strategy=? AND timeframe=?",
                    (inst.run_key, inst.timeframe))
    else:
        df = _query(inst.run_key, "SELECT time, symbol, equity, close FROM equity")
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    return df


def curves(inst, paper_db=DB_FILE):
    """(strategy return %, equal-weight buy-and-hold return %) indexed by candle time."""
    df = equity_frame(inst, paper_db)
    if df.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    wide = df.pivot_table(index="time", columns="symbol", values="equity").sort_index().ffill()
    n = len(inst.symbol_list)
    per_symbol = inst.equity if inst.mode == "paper" else inst.equity / max(n, 1)
    start_total = per_symbol * n
    idle = max(n - wide.shape[1], 0)
    total = wide.fillna(per_symbol).sum(axis=1) + idle * per_symbol
    strat = (total / start_total - 1) * 100
    closes = df.pivot_table(index="time", columns="symbol", values="close").sort_index()
    if closes.empty or closes.isna().all().all():
        bh = pd.Series(index=strat.index, dtype=float)
    else:
        bh = ((1 + closes.pct_change().mean(axis=1, skipna=True).fillna(0)).cumprod() - 1) * 100
    return strat, bh.reindex(strat.index)


def _sharpe(ret_pct):
    eq = 1 + ret_pct / 100
    d = eq.resample("1D").last().dropna().pct_change().dropna()
    if len(d) < 3 or not d.std(ddof=1) > 0:
        return None
    return float(d.mean() / d.std(ddof=1) * math.sqrt(365))


def trades(inst, paper_db=DB_FILE):
    """Paper: trades table. Testnet: orders (each row one market order)."""
    if inst.mode == "paper":
        return _query(paper_db, "SELECT * FROM trades WHERE strategy=? AND timeframe=? ORDER BY entry_time",
                      (inst.run_key, inst.timeframe))
    return _query(inst.run_key, "SELECT * FROM orders ORDER BY created_at")


def positions(inst, paper_db=DB_FILE):
    if inst.mode == "paper":
        return _query(paper_db, "SELECT symbol, side, entry_time, entry_price, stop, qty FROM trades "
                                "WHERE strategy=? AND timeframe=? AND exit_time IS NULL ORDER BY symbol",
                      (inst.run_key, inst.timeframe))
    return _query(inst.run_key, "SELECT * FROM positions ORDER BY symbol")


def signals(inst, limit=500, paper_db=DB_FILE):
    if inst.mode == "paper":
        return _query(paper_db, "SELECT candle_time, symbol, signal, rsi, close, position, action FROM signals "
                                "WHERE strategy=? AND timeframe=? ORDER BY id DESC LIMIT ?",
                      (inst.run_key, inst.timeframe, limit))
    return _query(inst.run_key, "SELECT time, level, symbol, message FROM events ORDER BY id DESC LIMIT ?", (limit,))


def summary(inst, paper_db=DB_FILE):
    strat, bh = curves(inst, paper_db)
    tr = trades(inst, paper_db)
    out = {"days": 0, "return_pct": None, "buy_hold_pct": None, "sharpe": None, "buy_hold_sharpe": None,
           "max_dd_pct": None, "buy_hold_max_dd_pct": None, "closed_trades": 0, "win_rate_pct": None,
           "open_positions": int(len(positions(inst, paper_db))), "last_candle": None}
    if len(strat):
        out.update(days=int((strat.index[-1] - strat.index[0]).days), return_pct=float(strat.iloc[-1]),
                   sharpe=_sharpe(strat), max_dd_pct=max_drawdown_pct(1 + strat / 100),
                   last_candle=strat.index[-1].isoformat())
        if bh.notna().any():
            b = bh.dropna()
            out.update(buy_hold_pct=float(b.iloc[-1]), buy_hold_sharpe=_sharpe(b),
                       buy_hold_max_dd_pct=max_drawdown_pct(1 + b / 100))
    if not tr.empty:
        if inst.mode == "paper":
            closed = tr[tr["exit_time"].notna()]
        else:
            closed = tr[(tr["side"] == "sell") & tr["pnl"].notna()]
        out["closed_trades"] = int(len(closed))
        if len(closed):
            out["win_rate_pct"] = float((closed["pnl"] > 0).mean() * 100)
    return out


def service_status(name, status_file=STATUS_FILE, max_age_s=15):
    """State of an instance's process as reported by run_all.py."""
    if not os.path.exists(status_file):
        return {"state": "supervisor not running"}
    try:
        with open(status_file) as f:
            st = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"state": "unknown"}
    age = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(st["updated_at"])).total_seconds()
    if age > max_age_s:
        return {"state": "supervisor not running"}
    if name in st.get("blocked", {}):
        return {"state": "blocked", "note": st["blocked"][name]}
    return st["services"].get(name, {"state": "stopped"})


def log_tail(name, lines=200, log_dir=LOG_DIR):
    path = os.path.join(log_dir, f"{name}.log")
    if not os.path.exists(path):
        return []
    with open(path, errors="replace") as f:
        return [line.rstrip("\n") for line in f.readlines()[-lines:]]
