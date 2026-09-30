# run_realtime.py
"""Paper trading loop on live Binance data.

Each newly closed candle is fed to the same Engine the backtest uses, so paper
results follow exactly the backtest rules (next-open fills, intrabar stops,
fees and slippage). State, trades, signals and the equity curve are stored in
SQLite (paper.db), one transaction per candle. After a restart each symbol
resumes from its saved state and first processes the candles it missed.
Times are UTC.
"""
import time
from functools import partial

import pandas as pd

import config
import strategy
from data import fetch_recent, now_ms, timeframe_ms
from engine import Engine
from indicators import add_indicators
from store import PaperStore

HISTORY = 300          # closed candles needed for indicator warm-up
MAX_FETCH = 1000       # Binance kline limit per request
CLOSE_DELAY_S = 3      # wait a few seconds after a candle closes before fetching


def _fmt_time(t):
    return pd.Timestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def describe_action(engine, signal):
    """What the engine did with this candle's signal."""
    if engine.pending is None:
        return "ignored (already in position)" if engine.position else "ignored (long-only)"
    if engine.pending[0] == "EXIT":
        return "exit at next open"
    return f"enter {engine.pending[1]} at next open"


class SymbolRunner:
    def __init__(self, symbol, timeframe, strategy_key, engine_cfg, store, params=None, label=None):
        self.symbol = symbol
        self.timeframe = timeframe
        self.key = label or strategy_key   # run name used in paper.db
        self.signal_fn = partial(strategy.get_strategy(strategy_key), **(params or {}))
        self.store = store
        self.engine = Engine(engine_cfg, symbol, on_open=self._opened, on_close=self._closed)
        state, self.last_time = store.load_state(symbol, strategy_key, timeframe)
        if state is not None:
            self.engine.load_state(state)

    # engine callbacks: written inside the current candle's transaction
    def _opened(self, symbol, p):
        self.store.trade_opened(self.key, self.timeframe, symbol, p)
        stop = "-" if p.stop is None else f"{p.stop:.8g}"
        print(f"🟢 [{symbol}] OPEN {p.side} @ {p.entry_price:.8g} | stop: {stop}")

    def _closed(self, t):
        self.store.trade_closed(t)
        print(f"🔴 [{t.symbol}] CLOSE {t.side} @ {t.exit_price:.8g} ({t.reason}) | "
              f"PnL: {t.pnl:.4f} ({t.return_pct:+.2f}%)")

    def _fetch_limit(self):
        if self.last_time is None:
            return HISTORY
        missed = (now_ms() - pd.Timestamp(self.last_time).value // 10**6) // timeframe_ms(self.timeframe)
        return int(min(MAX_FETCH, max(HISTORY, missed + HISTORY)))

    def update(self):
        df = fetch_recent(self.symbol, self.timeframe, self._fetch_limit())
        if df.empty:
            return
        df = add_indicators(df)
        if self.last_time is None:
            # First run: start from the next closed candle; don't simulate trades on past data.
            self.last_time = df["timestamp"].iloc[-1]
            with self.store.candle():
                self.store.save_state(self.symbol, self.key, self.timeframe,
                                      self.engine.to_state(), self.last_time)
            return

        signals = self.signal_fn(df)
        new = df["timestamp"] > self.last_time
        if new.any():
            first_new = df.loc[new, "timestamp"].iloc[0]
            gap = (first_new - pd.Timestamp(self.last_time)) // pd.Timedelta(milliseconds=timeframe_ms(self.timeframe)) - 1
            if gap > 0:
                print(f"⚠️ [{self.symbol}] {gap} candles missed beyond the fetch window; continuing")

        for (_, row), sig in zip(df[new].iterrows(), signals[new]):
            with self.store.candle():
                self.engine.on_bar(row["timestamp"], row["open"], row["high"], row["low"],
                                   row["close"], sig, row["ATR"], row["RSI"])
                if sig in ("BUY", "SELL"):
                    side = self.engine.position.side if self.engine.position else None
                    self.store.log_signal(self.key, self.timeframe, self.symbol, row["timestamp"], sig,
                                          row["RSI"], row["close"], side, describe_action(self.engine, sig))
                t, eq = self.engine.equity_curve[-1]
                self.store.log_equity(self.key, self.timeframe, self.symbol, t, eq)
                self.store.save_state(self.symbol, self.key, self.timeframe,
                                      self.engine.to_state(), row["timestamp"])
            self.engine.equity_curve.clear()  # already stored; keep memory flat
            self.last_time = row["timestamp"]

        last = df.iloc[-1]
        pos = self.engine.position
        pos_show = f"{pos.side} stop {pos.stop:.8g}" if pos and pos.stop else (pos.side if pos else "-")
        print(f"[{self.symbol}] {_fmt_time(last['timestamp'])} | Signal: {signals.iloc[-1]} | "
              f"RSI: {last['RSI']:.2f} | Close: {last['close']:.8g} | Position: {pos_show} | "
              f"Equity: {self.engine.equity:.2f}")


def sleep_until_next_close(timeframe):
    step = timeframe_ms(timeframe)
    now = now_ms()
    wait_ms = step - now % step + CLOSE_DELAY_S * 1000
    print(f"\n⏳ Next {timeframe} candle closes in {wait_ms / 1000:.0f}s...\n")
    time.sleep(wait_ms / 1000)


def main():
    cfg = config.paper_engine_config()
    store = PaperStore()
    label = config.strategy_label()
    runners = [SymbolRunner(s, config.TIMEFRAME, config.STRATEGY, cfg, store,
                            params=config.STRATEGY_PARAMS, label=label) for s in config.SYMBOLS]
    resumed = sum(r.last_time is not None for r in runners)
    print(f"📡 Paper trading started: {label}, timeframe {config.TIMEFRAME}, "
          f"{len(runners)} symbols ({resumed} resumed from paper.db), long-only={not cfg.allow_short}\n")
    while True:
        for r in runners:
            try:
                r.update()
            except Exception as e:
                print(f"[ERROR] {r.symbol}: {e}")
        sleep_until_next_close(config.TIMEFRAME)


if __name__ == "__main__":
    main()
