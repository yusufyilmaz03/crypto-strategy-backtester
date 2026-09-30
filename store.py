# store.py
"""SQLite persistence for paper trading: engine state, trades, signals and equity.

All writes for one candle happen in a single transaction (see PaperStore.candle),
so a crash never leaves the state and the logs out of sync.
"""
import json
import sqlite3
from contextlib import contextmanager

import pandas as pd

DB_FILE = "paper.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_state (
    symbol TEXT, strategy TEXT, timeframe TEXT,
    state TEXT NOT NULL, last_time TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, strategy, timeframe)
);
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY, symbol TEXT, strategy TEXT, timeframe TEXT, side TEXT,
    entry_time TEXT, exit_time TEXT, entry_price REAL, exit_price REAL, qty REAL,
    fees REAL, pnl REAL, return_pct REAL, reason TEXT, entry_rsi REAL, stop REAL
);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, candle_time TEXT, symbol TEXT, strategy TEXT,
    timeframe TEXT, signal TEXT, rsi REAL, close REAL, position TEXT, action TEXT
);
CREATE TABLE IF NOT EXISTS equity (
    symbol TEXT, strategy TEXT, timeframe TEXT, time TEXT, equity REAL,
    PRIMARY KEY (symbol, strategy, timeframe, time)
);
"""


def _t(x):
    return pd.Timestamp(x).isoformat() if x is not None else None


def _f(x):
    return None if x is None or pd.isna(x) else float(x)


class PaperStore:
    def __init__(self, path=DB_FILE):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    @contextmanager
    def candle(self):
        """Group all writes for one candle into one transaction."""
        try:
            yield self
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ---------- engine state ----------
    def load_state(self, symbol, strategy, timeframe):
        row = self.conn.execute(
            "SELECT state, last_time FROM engine_state WHERE symbol=? AND strategy=? AND timeframe=?",
            (symbol, strategy, timeframe)).fetchone()
        if row is None:
            return None, None
        return json.loads(row[0]), pd.Timestamp(row[1])

    def save_state(self, symbol, strategy, timeframe, state, last_time):
        self.conn.execute(
            "INSERT OR REPLACE INTO engine_state VALUES (?, ?, ?, ?, ?, ?)",
            (symbol, strategy, timeframe, json.dumps(state), _t(last_time),
             pd.Timestamp.now(tz="UTC").isoformat()))

    # ---------- logs ----------
    def trade_opened(self, strategy, timeframe, symbol, p):
        self.conn.execute(
            "INSERT OR REPLACE INTO trades (trade_id, symbol, strategy, timeframe, side, entry_time,"
            " entry_price, qty, entry_rsi, stop) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (p.trade_id, symbol, strategy, timeframe, p.side, _t(p.entry_time), p.entry_price,
             p.qty, _f(p.signal_rsi), _f(p.stop)))

    def trade_closed(self, t):
        self.conn.execute(
            "UPDATE trades SET exit_time=?, exit_price=?, fees=?, pnl=?, return_pct=?, reason=?"
            " WHERE trade_id=?",
            (_t(t.exit_time), t.exit_price, t.fees, t.pnl, t.return_pct, t.reason, t.trade_id))

    def log_signal(self, strategy, timeframe, symbol, candle_time, signal, rsi, close, position, action):
        self.conn.execute(
            "INSERT INTO signals (candle_time, symbol, strategy, timeframe, signal, rsi, close,"
            " position, action) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_t(candle_time), symbol, strategy, timeframe, signal, _f(rsi), _f(close),
             position or "NONE", action))

    def log_equity(self, strategy, timeframe, symbol, time, equity):
        self.conn.execute("INSERT OR REPLACE INTO equity VALUES (?, ?, ?, ?, ?)",
                          (symbol, strategy, timeframe, _t(time), float(equity)))

    # ---------- reads (dashboard) ----------
    def read(self, table, where="", params=()):
        return pd.read_sql_query(f"SELECT * FROM {table} {where}", self.conn, params=params)
