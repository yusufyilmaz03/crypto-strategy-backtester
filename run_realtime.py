# run_realtime.py
"""Paper trading loop on live Binance data.

Each newly closed candle is fed to the same Engine the backtest uses, so paper
results follow exactly the backtest rules (next-open fills, intrabar stops,
fees and slippage). Positions only exist in memory; they are lost on restart.
Logs: signals_log.csv (every BUY/SELL signal and what was done with it) and
trades_log.csv (one row per trade, filled in when it closes). Times are UTC.
"""
import csv
import os
import time

import pandas as pd

import config
import strategy
from data import fetch_recent, now_ms, timeframe_ms
from engine import Engine
from indicators import add_indicators

SIGNALS_FILE = "signals_log.csv"
TRADES_FILE = "trades_log.csv"
HISTORY = 300          # closed candles fetched per update (indicator warm-up)
CLOSE_DELAY_S = 3      # wait a few seconds after a candle closes before fetching

SIGNAL_COLUMNS = ["Candle Time", "Symbol", "Signal", "RSI", "Close", "Position", "Action"]
TRADE_COLUMNS = ["TradeID", "Timestamp Entry", "Timestamp Exit", "Symbol", "Position",
                 "Entry Price", "Exit Price", "Entry RSI", "PnL", "Return%", "Close Reason"]


def _fmt_time(t):
    return pd.Timestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def _append_row(path, columns, row):
    new_file = not os.path.isfile(path)
    if not new_file:
        with open(path, newline="") as f:
            header = next(csv.reader(f), None)
        if header != columns:
            # Log written by an older version with different columns: keep it aside.
            backup = f"{path}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
            os.rename(path, backup)
            print(f"ℹ️ {path} had an old format; moved to {backup}")
            new_file = True
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(columns)
        w.writerow(row)


def log_signal(symbol, candle_time, signal, rsi, close, position, action):
    _append_row(SIGNALS_FILE, SIGNAL_COLUMNS, [
        _fmt_time(candle_time), symbol, signal,
        f"{rsi:.2f}" if pd.notna(rsi) else "", f"{close:.8g}", position or "NONE", action,
    ])


def log_trade_open(symbol, p):
    _append_row(TRADES_FILE, TRADE_COLUMNS, [
        p.trade_id, _fmt_time(p.entry_time), "", symbol, p.side, f"{p.entry_price:.8g}", "",
        f"{p.signal_rsi:.2f}" if p.signal_rsi is not None else "", "", "", "",
    ])
    print(f"🟢 [{symbol}] OPEN {p.side} @ {p.entry_price:.8g} | stop: {p.stop if p.stop is None else f'{p.stop:.8g}'}")


def log_trade_close(t):
    """Fill in the exit fields of the trade's row (the file is rewritten)."""
    if not os.path.exists(TRADES_FILE):
        return
    with open(TRADES_FILE, newline="") as f:
        rows = list(csv.reader(f))
    for row in reversed(rows[1:]):
        if row and row[0] == t.trade_id:
            row[2] = _fmt_time(t.exit_time)
            row[6] = f"{t.exit_price:.8g}"
            row[8] = f"{t.pnl:.6f}"
            row[9] = f"{t.return_pct:.4f}"
            row[10] = t.reason
            break
    with open(TRADES_FILE, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"🔴 [{t.symbol}] CLOSE {t.side} @ {t.exit_price:.8g} ({t.reason}) | "
          f"PnL: {t.pnl:.4f} ({t.return_pct:+.2f}%)")


def describe_action(engine, signal):
    """What the engine did with this candle's signal."""
    if engine.pending is None:
        return "ignored (already in position)" if engine.position else "ignored (long-only)"
    if engine.pending[0] == "EXIT":
        return "exit at next open"
    return f"enter {engine.pending[1]} at next open"


class SymbolRunner:
    def __init__(self, symbol, timeframe, strategy_key, engine_cfg):
        self.symbol = symbol
        self.timeframe = timeframe
        self.signal_fn = strategy.get_strategy(strategy_key)
        self.engine = Engine(engine_cfg, symbol, on_open=log_trade_open, on_close=log_trade_close)
        self.last_time = None  # last candle fed to the engine

    def update(self):
        df = fetch_recent(self.symbol, self.timeframe, HISTORY)
        if df.empty:
            return
        df = add_indicators(df)
        if self.last_time is None:
            # Start from the next closed candle; don't simulate trades on past data.
            self.last_time = df["timestamp"].iloc[-1]
            return

        signals = self.signal_fn(df)
        new = df["timestamp"] > self.last_time
        for (_, row), sig in zip(df[new].iterrows(), signals[new]):
            self.engine.on_bar(row["timestamp"], row["open"], row["high"], row["low"], row["close"],
                               sig, row["ATR"], row["RSI"])
            if sig in ("BUY", "SELL"):
                side = self.engine.position.side if self.engine.position else None
                log_signal(self.symbol, row["timestamp"], sig, row["RSI"], row["close"], side,
                           describe_action(self.engine, sig))
            self.last_time = row["timestamp"]

        last = df.iloc[-1]
        pos = self.engine.position
        pos_show = f"{pos.side} stop {pos.stop:.8g}" if pos and pos.stop else (pos.side if pos else "-")
        print(f"[{self.symbol}] {_fmt_time(last['timestamp'])} | Signal: {signals.iloc[-1]} | "
              f"RSI: {last['RSI']:.2f} | Close: {last['close']:.8g} | Position: {pos_show}")


def sleep_until_next_close(timeframe):
    step = timeframe_ms(timeframe)
    now = now_ms()
    wait_ms = step - now % step + CLOSE_DELAY_S * 1000
    print(f"\n⏳ Next {timeframe} candle closes in {wait_ms / 1000:.0f}s...\n")
    time.sleep(wait_ms / 1000)


def main():
    cfg = config.engine_config()
    runners = [SymbolRunner(s, config.TIMEFRAME, config.STRATEGY, cfg) for s in config.SYMBOLS]
    print(f"📡 Paper trading started: strategy {config.STRATEGY}, timeframe {config.TIMEFRAME}, "
          f"{len(runners)} symbols, long-only={not cfg.allow_short}\n")
    while True:
        for r in runners:
            try:
                r.update()
            except Exception as e:
                print(f"[ERROR] {r.symbol}: {e}")
        sleep_until_next_close(config.TIMEFRAME)


if __name__ == "__main__":
    main()
