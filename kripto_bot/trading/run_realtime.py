# kripto_bot/trading/run_realtime.py
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

from kripto_bot import config
from kripto_bot.core import lower_timeframes, sizing, strategy
from kripto_bot.core.data import fetch_recent, now_ms, timeframe_ms
from kripto_bot.core.engine import Engine
from kripto_bot.core.indicators import add_indicators
from kripto_bot.trading import position_watch, store as store_mod
from kripto_bot.trading.store import PaperStore, single_instance

HISTORY = 300          # closed candles needed for indicator warm-up
MAX_FETCH = 1000       # Binance kline limit per request
CLOSE_DELAY_S = 3      # wait a few seconds after a candle closes before fetching
RETRY_S = 60           # after a failed update, try again this soon (doubling up to MAX_RETRY_S)
MAX_RETRY_S = 1800     # instead of waiting for the next close


def _fmt_time(t):
    return pd.Timestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def describe_action(engine, signal, entry_ok=True):
    """What the engine did with this candle's signal."""
    if engine.pending is None:
        if engine.position:
            return "ignored (already in position)"
        if signal == "BUY" and engine.reentry_above is not None:
            return "ignored (waiting after a trailing or lower-timeframe exit)"
        if signal == "BUY" and not entry_ok:
            return "ignored (lower timeframes don't confirm)"
        return "ignored (long-only)"
    if engine.pending[0] == "EXIT":
        return "trailing exit at next open" if engine.pending[1:] == ("TRAIL",) else "exit at next open"
    return f"enter {engine.pending[1]} at next open"


class SymbolRunner:
    def __init__(self, symbol, timeframe, strategy_key, engine_cfg, store, params=None, label=None,
                 lower_tf_entry=False, lower_tf_exit=False):
        self.symbol = symbol
        self.timeframe = timeframe
        self.step_delta = pd.Timedelta(milliseconds=timeframe_ms(timeframe))
        self.lower_tf_entry, self.lower_tf_exit = lower_tf_entry, lower_tf_exit
        self.lower = None                  # lower-timeframe candles and signals of the last prepare()
        self.key = label or strategy_key   # run name used in paper.db
        self.signal_fn = partial(strategy.get_strategy(strategy_key), **(params or {}))
        self.store = store
        self.engine = Engine(engine_cfg, symbol, on_open=self._opened, on_close=self._closed)
        self.history = None                # candles of the last prepare()
        state, self.last_time = store.load_state(symbol, self.key, timeframe)   # saved under self.key too
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

    def prepare(self):
        """Fetch candles and return the closed ones not processed yet (with a 'signal'
        column), or an empty frame. On the first run only the starting point is saved."""
        df = fetch_recent(self.symbol, self.timeframe, self._fetch_limit())
        if df.empty:
            return df
        df = add_indicators(df)
        self.store.fill_missing_closes(self.key, self.timeframe, self.symbol, df)
        self.history = df
        if self.last_time is None:
            # First run: start from the next closed candle; don't simulate trades on past data.
            self.last_time = df["timestamp"].iloc[-1]
            with self.store.candle():
                self.store.save_state(self.symbol, self.key, self.timeframe,
                                      self.engine.to_state(), self.last_time)
            return df.iloc[0:0]
        df = df.assign(signal=self.signal_fn(df))
        new = df["timestamp"] > self.last_time
        if new.any():
            first_new = df.loc[new, "timestamp"].iloc[0]
            if self.lower_tf_entry or self.lower_tf_exit:
                self.lower = lower_timeframes.load_signals(self.symbol, self.timeframe, self.signal_fn, first_new,
                                                           fetch=lower_timeframes.closed_candles)
            gap = (first_new - pd.Timestamp(self.last_time)) // pd.Timedelta(milliseconds=timeframe_ms(self.timeframe)) - 1
            if gap > 0:
                print(f"⚠️ [{self.symbol}] {gap} candles missed beyond the fetch window; continuing")
        return df[new]

    def exit_watchable(self):
        """Is there something rule X could sell in the forming main candle: a long position
        (unless it is sold at the open anyway) or a long entry pending at the open?"""
        e = self.engine
        if e.pending is not None and e.pending[0] == "EXIT":
            return False
        if e.position is not None:
            return e.position.side == "LONG"
        return e.pending is not None and e.pending[0] == "ENTER" and e.pending[1] == "LONG"

    def early_exit_time(self, period, open_, frames):
        """Rule X for the main candle that opened at `period` (open `open_`): the time to
        exit at, or None."""
        if not self.exit_watchable():
            return None
        e = self.engine
        stop = e.position.stop if e.position is not None else e.entry_stop(open_)
        return lower_timeframes.early_exit_time(frames, period, self.timeframe, stop)

    def exit_early(self, period, open_, time, price):
        """Sell at `price` at `time` inside the main candle that opened at `period`. Call
        inside the store's candle() transaction."""
        fine = lower_timeframes.LOWER_TIMEFRAMES[self.timeframe][-1]
        if self.engine.exit_inside(period, open_, time, price) is not None:
            self.store.log_signal(self.key, self.timeframe, self.symbol, time, "SELL", None, price, None,
                                  f"lower-timeframe exit at the {fine} open")

    def step(self, row):
        """Process one closed candle. Call inside the store's candle() transaction."""
        sig, period = row["signal"], row["timestamp"]
        if self.lower_tf_exit:
            # the watcher normally exits during the candle; this covers what it missed
            t = self.early_exit_time(period, row["open"], self.lower)
            if t is not None:
                fine = self.lower[lower_timeframes.LOWER_TIMEFRAMES[self.timeframe][-1]]
                opens = fine.loc[fine["timestamp"] == t, "open"]
                if len(opens):
                    self.exit_early(period, row["open"], t, float(opens.iloc[0]))
                else:
                    print(f"⚠️ [{self.symbol}] lower-timeframe exit at {_fmt_time(t)} skipped: no candle at that time")
        entry_ok = True
        if self.lower_tf_entry and sig == "BUY":
            entry_ok = lower_timeframes.all_say(self.lower, period + self.step_delta, "BUY")
        self.engine.on_bar(period, row["open"], row["high"], row["low"],
                           row["close"], sig, row["ATR"], row["RSI"], entry_ok=entry_ok)
        if sig in ("BUY", "SELL"):
            side = self.engine.position.side if self.engine.position else None
            self.store.log_signal(self.key, self.timeframe, self.symbol, row["timestamp"], sig,
                                  row["RSI"], row["close"], side, describe_action(self.engine, sig, entry_ok))
        t, eq = self.engine.equity_curve[-1]
        self.store.log_equity(self.key, self.timeframe, self.symbol, t, eq, row["close"])
        self.store.save_state(self.symbol, self.key, self.timeframe,
                              self.engine.to_state(), row["timestamp"])
        self.engine.equity_curve.clear()  # already stored; keep memory flat
        self.last_time = row["timestamp"]

    def report(self):
        if self.history is None or self.history.empty:
            return
        last = self.history.iloc[-1]
        pos = self.engine.position
        pos_show = f"{pos.side} stop {pos.stop:.8g}" if pos and pos.stop else (pos.side if pos else "-")
        sig = self.signal_fn(self.history).iloc[-1]
        print(f"[{self.symbol}] {_fmt_time(last['timestamp'])} | Signal: {sig} | "
              f"RSI: {last['RSI']:.2f} | Close: {last['close']:.8g} | Position: {pos_show} | "
              f"Equity: {self.engine.equity:.2f}")

    def update(self):
        for _, row in self.prepare().iterrows():
            with self.store.candle():
                self.step(row)
        self.report()


def period_ends(t, timeframe):
    """True if candle t is the last one of its calendar month."""
    nxt = pd.Timestamp(t) + pd.Timedelta(milliseconds=timeframe_ms(timeframe))
    return nxt.month != pd.Timestamp(t).month


class PortfolioRunner:
    """Runs all symbols of an instance in step and rebalances them to inverse-volatility
    weights after the last candle of each month (and once at the start). A rebalance
    resizes each symbol's engine to weight × portfolio value; a symbol in a position
    trades the difference at the close with fee and slippage. Candle times are processed
    in order across symbols, each time in one transaction together with its rebalance."""

    def __init__(self, runners, store, key, timeframe, lookback=sizing.LOOKBACK):
        self.runners, self.store, self.key, self.timeframe = runners, store, key, timeframe
        self.lookback = lookback

    def rebalance(self, t, closes):
        """closes: {symbol: close Series ending at t}."""
        price = {s: float(c.iloc[-1]) for s, c in closes.items()}
        engines = {r.symbol: r.engine for r in self.runners if r.symbol in closes}
        marked = {s: e.marked_equity(price[s]) for s, e in engines.items()}
        total = sum(marked.values())
        weights = sizing.inverse_vol_weights(closes, self.lookback)
        costs = {}
        for s, e in engines.items():
            costs[s] = e.resize(weights[s] * total, price[s])
            self.store.log_weight(self.key, self.timeframe, t, s, weights[s], costs[s])
            self.store.save_state(s, self.key, self.timeframe, e.to_state(), t)
            if self.store.has_equity(self.key, self.timeframe, s, t):
                self.store.log_equity(self.key, self.timeframe, s, t, e.marked_equity(price[s]), price[s])
        top = sorted(weights.items(), key=lambda x: -x[1])[:3]
        print(f"⚖️  Rebalanced {len(engines)} symbols at {_fmt_time(t)}: value {total:.2f}, "
              f"cost {sum(costs.values()):.2f}; largest weights "
              + ", ".join(f"{s} {w * 100:.1f}%" for s, w in top))

    def update(self):
        """Process the new candles of all symbols; returns the symbols that failed."""
        pending, failed = {}, []
        for r in self.runners:
            try:
                pending[r] = r.prepare()
            except Exception as e:
                print(f"[ERROR] {r.symbol}: {e}")
                failed.append(r.symbol)
        ready = [r for r in pending if r.history is not None and not r.history.empty]
        if self.store.last_rebalance(self.key, self.timeframe) is None and ready:
            # Start: split the capital by weight before the first candle is traded.
            t = max(r.last_time for r in ready)
            with self.store.candle():
                self.rebalance(t, {r.symbol: r.history.set_index("timestamp")["close"].loc[:t] for r in ready})
        times = sorted({t for df in pending.values() for t in df["timestamp"]})
        for t in times:
            with self.store.candle():
                for r, df in pending.items():
                    rows = df[df["timestamp"] == t]
                    if len(rows):
                        r.step(rows.iloc[0])
                if period_ends(t, self.timeframe):
                    have = [r for r in ready if r.last_time == t]
                    if len(have) < len(ready):
                        print(f"⚠️ Rebalance at {_fmt_time(t)} without "
                              f"{', '.join(r.symbol for r in ready if r not in have)} (no candle yet)")
                    self.rebalance(t, {r.symbol: r.history.set_index("timestamp")["close"].loc[:t] for r in have})
        for r in self.runners:
            r.report()
        return failed


def update_runners(runners, portfolio=None):
    """Process new candles; returns the symbols whose update failed."""
    if portfolio:
        return portfolio.update()
    failed = []
    for r in runners:
        try:
            r.update()
        except Exception as e:
            print(f"[ERROR] {r.symbol}: {e}")
            failed.append(r.symbol)
    return failed


def next_close_ms(timeframe):
    step = timeframe_ms(timeframe)
    return (now_ms() // step + 1) * step + CLOSE_DELAY_S * 1000


def _next_minute():
    return (now_ms() // 60_000 + 1) * 60_000 + CLOSE_DELAY_S * 1000


def sleep_until_next_close(timeframe, check_every_s=30, on_minute=None):
    """Wait until the next candle close (+ CLOSE_DELAY_S), checking the wall clock in short
    steps: on macOS a single long sleep doesn't count time the computer spends asleep.
    on_minute, if given, is called a few seconds after each 1m close while waiting."""
    target = next_close_ms(timeframe)
    print(f"\n⏳ Next {timeframe} candle closes in {(target - now_ms()) / 1000:.0f}s "
          f"({pd.Timestamp(target, unit='ms'):%Y-%m-%d %H:%M:%S} UTC)...\n")
    wait_until(target, check_every_s, on_minute)


def wait_until(target, check_every_s=30, on_minute=None):
    """Wait until wall-clock time `target` (ms); see sleep_until_next_close."""
    minute = _next_minute()
    while (remaining := target - now_ms()) > 0:
        if on_minute is not None:
            if now_ms() >= minute:
                on_minute()
                minute = _next_minute()
            remaining = min(remaining, max(minute - now_ms(), 0))
        time.sleep(min(check_every_s, remaining / 1000))


class RebuildError(RuntimeError):
    pass


def rebuild_state(inst, store, log=print, tol=1e-6):
    """Recompute each symbol's saved engine state by replaying the candles the instance
    already recorded, and save it only if the replay reproduces the recorded equity,
    closed trades and open position exactly. Recorded results are not changed.

    Repairs states lost to the bug that saved under the run key but loaded under the
    strategy key (every restart then started the engines from scratch)."""
    fn = partial(strategy.get_strategy(inst.strategy), **inst.params)
    cfg = inst.engine_config()
    eq = store.read("equity", "WHERE strategy=? AND timeframe=?", (inst.run_key, inst.timeframe))
    trades = store.read("trades", "WHERE strategy=? AND timeframe=?", (inst.run_key, inst.timeframe))
    step = pd.Timedelta(milliseconds=timeframe_ms(inst.timeframe))
    rebuilt, problems = {}, []
    for symbol in inst.symbol_list:
        rec = eq[eq.symbol == symbol].assign(time=lambda d: pd.to_datetime(d.time)).sort_values("time")
        if rec.empty:
            log(f"  {symbol}: nothing recorded, skipped")
            continue
        first, last = rec.time.iloc[0], rec.time.iloc[-1]
        need = int((pd.Timestamp(now_ms(), unit="ms") - first) / step) + HISTORY + 2
        if need > MAX_FETCH:
            raise RebuildError(f"{symbol}: {need} candles needed, more than one request can fetch")
        df = add_indicators(fetch_recent(symbol, inst.timeframe, need))
        sig = fn(df)
        engine = Engine(cfg, symbol)
        sel = (df.timestamp >= first) & (df.timestamp <= last)
        for (_, row), x in zip(df[sel].iterrows(), sig[sel]):
            engine.on_bar(row["timestamp"], row["open"], row["high"], row["low"], row["close"],
                          x, row["ATR"], row["RSI"])
        replay = pd.Series(dict(engine.equity_curve))
        recorded = rec.set_index("time")["equity"]
        diff = (replay.reindex(recorded.index) / recorded - 1).abs().max()
        mine = trades[trades.symbol == symbol]
        closed = mine[mine.exit_time.notna()].sort_values("entry_time")
        open_ = mine[mine.exit_time.isna()]
        issues = []
        if len(replay) != len(recorded) or not diff <= tol:
            issues.append(f"equity differs (max {diff:.2e})")
        if len(engine.trades) != len(closed) or any(
                abs(t.pnl - p) > tol * 1000 for t, p in zip(engine.trades, closed.pnl)):
            issues.append(f"closed trades differ ({len(engine.trades)} replayed vs {len(closed)} recorded)")
        if (engine.position is None) != open_.empty or len(open_) > 1:
            issues.append(f"open position differs (replay {'open' if engine.position else 'flat'}, "
                          f"{len(open_)} open recorded)")
        elif engine.position is not None:
            o = open_.iloc[0]
            if pd.Timestamp(o.entry_time) != pd.Timestamp(engine.position.entry_time) or \
                    abs(o.entry_price / engine.position.entry_price - 1) > tol:
                issues.append("open position entry differs")
            engine.position.trade_id = o.trade_id      # later exits update the recorded row
        if issues:
            problems.append(f"{symbol}: {'; '.join(issues)}")
        rebuilt[symbol] = (engine.to_state(), last)
        log(f"  {symbol}: {len(recorded)} candles, {len(engine.trades)} closed, "
            f"{'in position' if engine.position else 'flat'}{', MISMATCH' if issues else ''}")
    if problems:
        raise RebuildError("replay doesn't match the records, nothing saved:\n  " + "\n  ".join(problems))
    with store.candle():
        for symbol, (state, last) in rebuilt.items():
            store.save_state(symbol, inst.run_key, inst.timeframe, state, last)
    return rebuilt


def main(instance_name="forward-v9", rebuild=False):
    from kripto_bot import instances
    inst = instances.get(instance_name)
    if inst.mode != "paper":
        raise SystemExit(f"{inst.name} is a {inst.mode} instance; use `python -m kripto_bot testnet`")
    lock = single_instance(store_mod.DB_FILE, inst.name)  # noqa: F841 (held until exit)
    cfg = inst.engine_config()
    store = PaperStore()
    if rebuild:
        print(f"🔧 Rebuilding the engine states of '{inst.name}' from its records...")
        try:
            rebuilt = rebuild_state(inst, store)
        except RebuildError as e:
            raise SystemExit(f"❌ {e}")
        print(f"✅ {len(rebuilt)} symbol states verified and saved. Start the instance again.")
        return
    runners = [SymbolRunner(s, inst.timeframe, inst.strategy, cfg, store, params=inst.params, label=inst.run_key,
                            lower_tf_entry=inst.lower_tf_entry, lower_tf_exit=inst.lower_tf_exit)
               for s in inst.symbol_list]
    resumed = sum(r.last_time is not None for r in runners)
    print(f"📡 Paper instance '{inst.name}': {inst.strategy} {inst.params} stop={inst.stop}, "
          f"sizing {inst.sizing}, timeframe {inst.timeframe}, {len(runners)} symbols, "
          f"lower-timeframe entry {'on' if inst.lower_tf_entry else 'off'}, exit {'on' if inst.lower_tf_exit else 'off'} "
          f"({resumed} resumed from paper.db)\n")
    portfolio = PortfolioRunner(runners, store, inst.run_key, inst.timeframe) if inst.sizing == "inverse_vol" else None
    watch = position_watch.for_instance(inst, runners, store)
    run_loop(inst, runners, portfolio, watch)


def run_loop(inst, runners, portfolio=None, watch=None):
    """Update at every close. When an update fails (e.g. no network right after the
    computer wakes up), retry the failed symbols soon instead of a whole candle later."""
    todo, retry_s = runners, RETRY_S
    on_minute = watch.check if watch else None
    while True:
        if watch:
            watch.check()          # the last minutes of the candle that just closed
        failed = update_runners(todo, portfolio)
        retry_at = now_ms() + retry_s * 1000
        if failed and retry_at < next_close_ms(inst.timeframe):
            if not portfolio:
                todo = [r for r in runners if r.symbol in failed]
            print(f"🔁 {len(failed)} symbol(s) failed ({', '.join(failed[:5])}{', …' if len(failed) > 5 else ''}); "
                  f"retrying in {retry_s}s")
            wait_until(retry_at, on_minute=on_minute)
            retry_s = min(retry_s * 2, MAX_RETRY_S)
            continue
        todo, retry_s = runners, RETRY_S
        sleep_until_next_close(inst.timeframe, on_minute=on_minute)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Paper trading for one instance (see instances.json).")
    ap.add_argument("--instance", default="forward-v9")
    ap.add_argument("--rebuild-state", action="store_true",
                    help="recompute saved engine states from the recorded candles (instance must be stopped)")
    a = ap.parse_args()
    main(a.instance, a.rebuild_state)
