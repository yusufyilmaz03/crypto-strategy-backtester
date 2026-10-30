# kripto_bot/trading/store.py
"""SQLite persistence for paper trading: engine state, trades, signals and equity.

All writes for one candle happen in a single transaction (see PaperStore.candle),
so a crash never leaves the state and the logs out of sync.
"""
import fcntl
import json
import os
import sqlite3
from contextlib import contextmanager

import pandas as pd

from kripto_bot import paths

DB_FILE = paths.runtime("paper.db")

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
    symbol TEXT, strategy TEXT, timeframe TEXT, time TEXT, equity REAL, close REAL,
    PRIMARY KEY (symbol, strategy, timeframe, time)
);
CREATE TABLE IF NOT EXISTS weights (
    strategy TEXT, timeframe TEXT, time TEXT, symbol TEXT, weight REAL, cost REAL,
    PRIMARY KEY (strategy, timeframe, time, symbol)
);
CREATE TABLE IF NOT EXISTS stop_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, strategy TEXT, timeframe TEXT, symbol TEXT,
    entry_time TEXT, stop REAL, minute_time TEXT, minute_open REAL, minute_low REAL,
    est_fill REAL, detected_at TEXT,
    UNIQUE (strategy, timeframe, symbol, entry_time)
);
"""


class AlreadyRunning(RuntimeError):
    pass


def single_instance(db_path, name=None):
    """Hold an exclusive lock next to the database for the life of the process, so two
    copies of a bot (or of one instance) can't process the same candles or send the
    same orders twice."""
    lock_path = f"{db_path}.{name}.lock" if name else f"{db_path}.lock"
    handle = open(lock_path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise AlreadyRunning(f"another process is already using {db_path} ({lock_path} is locked)")
    handle.write(str(os.getpid()))
    handle.flush()
    return handle   # keep a reference; the lock is released when the process exits


def _t(x):
    return pd.Timestamp(x).isoformat() if x is not None else None


def _f(x):
    return None if x is None or pd.isna(x) else float(x)


def _connect(path):
    """SQLite connection that several bot processes can share (WAL, wait on locks)."""
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


class PaperStore:
    def __init__(self, path=None):
        path = path or DB_FILE
        self.conn = _connect(path)
        self.conn.executescript(SCHEMA)
        # databases created before the close column existed
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(equity)")]
        if "close" not in cols:
            self.conn.execute("ALTER TABLE equity ADD COLUMN close REAL")
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

    def log_equity(self, strategy, timeframe, symbol, time, equity, close=None):
        """Marked-to-market equity and the candle's close (for buy-and-hold comparisons)."""
        self.conn.execute(
            "INSERT OR REPLACE INTO equity (symbol, strategy, timeframe, time, equity, close)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (symbol, strategy, timeframe, _t(time), float(equity), _f(close)))

    def has_equity(self, strategy, timeframe, symbol, time):
        return self.conn.execute("SELECT 1 FROM equity WHERE strategy=? AND timeframe=? AND symbol=? AND time=?",
                                 (strategy, timeframe, symbol, _t(time))).fetchone() is not None

    def log_weight(self, strategy, timeframe, time, symbol, weight, cost):
        """Target weight set at a portfolio rebalance and the trading cost it caused."""
        self.conn.execute("INSERT OR REPLACE INTO weights VALUES (?, ?, ?, ?, ?, ?)",
                          (strategy, timeframe, _t(time), symbol, float(weight), float(cost)))

    def log_stop_event(self, strategy, timeframe, symbol, entry_time, stop, minute_time, minute_open,
                       minute_low, est_fill, detected_at):
        """Shadow stop: the first 1m candle that reached the stop of the trade entered at
        entry_time (trading/position_watch.py). One row per trade."""
        self.conn.execute(
            "INSERT OR IGNORE INTO stop_events (strategy, timeframe, symbol, entry_time, stop, minute_time,"
            " minute_open, minute_low, est_fill, detected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (strategy, timeframe, symbol, _t(entry_time), float(stop), _t(minute_time), float(minute_open),
             float(minute_low), float(est_fill), _t(detected_at)))

    def stop_event_recorded(self, strategy, timeframe, symbol, entry_time):
        return self.conn.execute("SELECT 1 FROM stop_events WHERE strategy=? AND timeframe=? AND symbol=?"
                                 " AND entry_time=?", (strategy, timeframe, symbol, _t(entry_time))).fetchone() is not None

    def last_rebalance(self, strategy, timeframe):
        row = self.conn.execute("SELECT MAX(time) FROM weights WHERE strategy=? AND timeframe=?",
                                (strategy, timeframe)).fetchone()
        return pd.Timestamp(row[0]) if row and row[0] else None

    def fill_missing_closes(self, strategy, timeframe, symbol, candles):
        """Add closes to equity rows written before closes were stored (candles: timestamp, close)."""
        rows = [(_f(c), symbol, strategy, timeframe, _t(t)) for t, c in zip(candles["timestamp"], candles["close"])]
        self.conn.executemany("UPDATE equity SET close=? WHERE symbol=? AND strategy=? AND timeframe=? AND time=?"
                              " AND close IS NULL", rows)
        self.conn.commit()

    # ---------- reads (dashboard) ----------
    def read(self, table, where="", params=()):
        return pd.read_sql_query(f"SELECT * FROM {table} {where}", self.conn, params=params)


TRADING_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (symbol TEXT PRIMARY KEY, cash REAL NOT NULL);
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY, qty REAL, cost REAL, entry_price REAL, entry_time TEXT, client_id TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    client_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, candle_time TEXT, ref_price REAL,
    status TEXT, filled REAL, net_base REAL, cost REAL, average REAL, fee_quote REAL,
    order_id TEXT, pnl REAL, created_at TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS runner_state (symbol TEXT PRIMARY KEY, last_time TEXT);
CREATE TABLE IF NOT EXISTS equity (time TEXT, symbol TEXT, equity REAL, close REAL,
                                   PRIMARY KEY (time, symbol));
CREATE TABLE IF NOT EXISTS daily (day TEXT PRIMARY KEY, start_equity REAL, blocked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, level TEXT,
                                   symbol TEXT, message TEXT);
"""


class TradingStore:
    """State of order-sending trading (testnet or live): per-symbol cash, positions the
    bot opened itself, orders, equity snapshots, daily loss tracking and events."""

    def __init__(self, path):
        self.conn = _connect(path)
        self.conn.executescript(TRADING_SCHEMA)
        self.conn.commit()

    def commit(self):
        self.conn.commit()

    # ---------- accounts / positions ----------
    def cash(self, symbol, initial):
        row = self.conn.execute("SELECT cash FROM accounts WHERE symbol=?", (symbol,)).fetchone()
        if row is None:
            self.conn.execute("INSERT INTO accounts VALUES (?, ?)", (symbol, initial))
            self.conn.commit()
            return initial
        return row[0]

    def set_cash(self, symbol, cash):
        self.conn.execute("UPDATE accounts SET cash=? WHERE symbol=?", (cash, symbol))

    def position(self, symbol):
        row = self.conn.execute(
            "SELECT qty, cost, entry_price, entry_time, client_id FROM positions WHERE symbol=?",
            (symbol,)).fetchone()
        if row is None:
            return None
        return dict(zip(["qty", "cost", "entry_price", "entry_time", "client_id"], row))

    def set_position(self, symbol, qty, cost, entry_price, entry_time, client_id):
        self.conn.execute("INSERT OR REPLACE INTO positions VALUES (?, ?, ?, ?, ?, ?)",
                          (symbol, qty, cost, entry_price, _t(entry_time), client_id))

    def clear_position(self, symbol):
        self.conn.execute("DELETE FROM positions WHERE symbol=?", (symbol,))

    def positions(self):
        return {r[0]: r[1] for r in self.conn.execute("SELECT symbol, qty FROM positions")}

    # ---------- orders ----------
    def record_order(self, fill, candle_time, ref_price, pnl=None, error=None):
        self.conn.execute(
            "INSERT OR REPLACE INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fill.client_id, fill.symbol, fill.side, _t(candle_time), _f(ref_price), fill.status,
             fill.filled, fill.net_base, fill.cost, fill.average, fill.fee_quote, fill.order_id,
             _f(pnl), pd.Timestamp.now(tz="UTC").isoformat(), error))

    def order_recorded(self, client_id):
        return self.conn.execute("SELECT 1 FROM orders WHERE client_id=?", (client_id,)).fetchone() is not None

    # ---------- progress / equity ----------
    def last_time(self, symbol):
        row = self.conn.execute("SELECT last_time FROM runner_state WHERE symbol=?", (symbol,)).fetchone()
        return pd.Timestamp(row[0]) if row else None

    def set_last_time(self, symbol, t):
        self.conn.execute("INSERT OR REPLACE INTO runner_state VALUES (?, ?)", (symbol, _t(t)))

    def log_equity(self, t, symbol, equity, close):
        self.conn.execute("INSERT OR REPLACE INTO equity VALUES (?, ?, ?, ?)",
                          (_t(t), symbol, float(equity), _f(close)))

    def latest_equity(self, symbol):
        row = self.conn.execute("SELECT equity FROM equity WHERE symbol=? ORDER BY time DESC LIMIT 1",
                                (symbol,)).fetchone()
        return row[0] if row else None

    def day(self, day):
        row = self.conn.execute("SELECT start_equity, blocked FROM daily WHERE day=?", (day,)).fetchone()
        return (row[0], bool(row[1])) if row else (None, False)

    def start_day(self, day, start_equity):
        self.conn.execute("INSERT OR IGNORE INTO daily (day, start_equity) VALUES (?, ?)", (day, start_equity))
        self.conn.commit()

    def block_day(self, day):
        self.conn.execute("UPDATE daily SET blocked=1 WHERE day=?", (day,))
        self.conn.commit()

    # ---------- events ----------
    def event(self, level, symbol, message):
        self.conn.execute("INSERT INTO events (time, level, symbol, message) VALUES (?, ?, ?, ?)",
                          (pd.Timestamp.now(tz="UTC").isoformat(), level, symbol, message))
        self.conn.commit()
        print(f"[{level}] {symbol or '-'}: {message}")

    def read(self, table):
        return pd.read_sql_query(f"SELECT * FROM {table}", self.conn)
