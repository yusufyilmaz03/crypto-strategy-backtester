# kripto_bot/trading/position_watch.py
"""Intraday watch over a paper instance's positions (docs/intrabar-watch.md).

A 4h or 1d instance only trades at its main candle close. Between closes this module
reads the 1m candles of the main candle that is forming and records the first minute a
stop would have been hit (the shadow stop), with an estimated fill of min(1m open, stop).

It never changes a position: the engine still closes it at the main close, at
min(open, stop) as in the backtest, so paper stays identical to the backtest. The
records show how far a fill seen minute by minute is from that assumption, and how
long after the hit the bot noticed it.

With the instance setting lower_tf_exit it also runs rule X live (EarlyExitWatch): after
each close of the finest lower timeframe it sells the position if every lower timeframe
says SELL. This one does change the position, with the same decision SymbolRunner.step
makes for candles the watcher missed, so a restart can't change the trades.

ManualClose sells a position when the panel asks for it (close_requests in paper.db).
"""
import pandas as pd

from kripto_bot import config
from kripto_bot.core import lower_timeframes
from kripto_bot.core.data import get_exchange, now_ms, timeframe_ms
from kripto_bot.core.lower_timeframes import WATCH_TIMEFRAME, closed_candles, has_lower_timeframes


def eligible(inst):
    """Paper instances on a timeframe with lower timeframes and with a stop."""
    return (inst.mode == "paper" and has_lower_timeframes(inst.timeframe)
            and config.STOPLOSS_CONFIG.get(inst.stop) is not None)


def _fmt(t):
    return pd.Timestamp(t).strftime("%Y-%m-%d %H:%M")


class StopWatch:
    """Shadow stop for the SymbolRunners of one instance. check() is called once a
    minute while the instance waits for the next main close, and once more right after
    that close, before the engine processes the candle (its last minutes)."""

    def __init__(self, runners, store, key, timeframe, fetch=closed_candles,
                 clock=lambda: pd.Timestamp.now(tz="UTC")):
        self.runners, self.store, self.key, self.timeframe = runners, store, key, timeframe
        self.step = pd.Timedelta(milliseconds=timeframe_ms(timeframe))
        self.fetch, self.clock = fetch, clock
        self.cursor = {}       # symbol -> (entry_time, stop, next minute to check)
        self.done = set()      # (symbol, entry_time) already recorded

    def check(self):
        for r in self.runners:
            try:
                self._check(r)
            except Exception as e:     # watching must never stop the trading loop
                print(f"[WATCH ERROR] {r.symbol}: {type(e).__name__}: {e}")

    def _watched(self, r):
        """(entry_time, stop or None) of the long position open during the forming main
        candle, or None if there is nothing to watch."""
        if r.last_time is None:
            return None
        period = pd.Timestamp(r.last_time) + self.step
        e = r.engine
        if e.pending is not None and e.pending[0] == "EXIT":
            return None                                   # sold at this candle's open
        if e.position is not None:
            p = e.position
            return (pd.Timestamp(p.entry_time), p.stop) if p.side == "LONG" and p.stop is not None else None
        if e.pending is not None and e.pending[0] == "ENTER" and e.pending[1] == "LONG":
            return period, None                           # opens at this candle's open; stop from that open
        return None

    def _check(self, r):
        watched = self._watched(r)
        if watched is None:
            self.cursor.pop(r.symbol, None)
            return
        entry_time, stop = watched
        if (r.symbol, entry_time) in self.done:
            return
        period = pd.Timestamp(r.last_time) + self.step
        cur = self.cursor.get(r.symbol)
        if cur is None or cur[0] != entry_time:
            if self.store.stop_event_recorded(self.key, self.timeframe, r.symbol, entry_time):
                self.done.add((r.symbol, entry_time))
                return
            cur = (entry_time, stop, period)
        since = max(cur[2], period)                       # earlier candles were checked by the engine
        minutes = self.fetch(r.symbol, WATCH_TIMEFRAME, since)
        minutes = minutes[(minutes["timestamp"] >= since) & (minutes["timestamp"] < period + self.step)]
        if minutes.empty:
            self.cursor[r.symbol] = cur
            return
        if cur[1] is None:
            first = minutes.iloc[0]
            if first["timestamp"] != period:
                self.cursor.pop(r.symbol, None)           # entry candle's open unknown: can't place the stop
                return
            cur = (entry_time, r.engine.entry_stop(float(first["open"])), since)
            if cur[1] is None:
                self.cursor.pop(r.symbol, None)
                return
        stop = cur[1]
        hit = minutes[minutes["low"] <= stop]
        if hit.empty:
            self.cursor[r.symbol] = (entry_time, stop, minutes["timestamp"].iloc[-1] + pd.Timedelta(minutes=1))
            return
        m = hit.iloc[0]
        est = min(float(m["open"]), stop)
        with self.store.candle():
            self.store.log_stop_event(self.key, self.timeframe, r.symbol, entry_time, stop, m["timestamp"],
                                      float(m["open"]), float(m["low"]), est, self.clock())
        self.done.add((r.symbol, entry_time))
        self.cursor.pop(r.symbol, None)
        print(f"⚠️ [{r.symbol}] stop {stop:.8g} hit at {_fmt(m['timestamp'])} UTC (1m low {float(m['low']):.8g}), "
              f"est. fill {est:.8g} ({(est / stop - 1) * 100:+.2f}% vs stop); "
              f"the engine closes it at the {self.timeframe} close")


class EarlyExitWatch:
    """Rule X for the SymbolRunners of one instance (docs/intrabar-watch.md). After each
    close of the finest lower timeframe inside the forming main candle, the position is
    sold if every lower timeframe says SELL, at the open that follows: read from the 1m
    candle starting at that time, which opens with the same trade as the finest candle."""

    def __init__(self, runners, store, timeframe, fetch=closed_candles,
                 clock=lambda: pd.Timestamp(now_ms(), unit="ms")):
        self.runners, self.store, self.timeframe = runners, store, timeframe
        self.step = pd.Timedelta(milliseconds=timeframe_ms(timeframe))
        self.fine_tf = lower_timeframes.LOWER_TIMEFRAMES[timeframe][-1]
        self.fine_step = pd.Timedelta(milliseconds=timeframe_ms(self.fine_tf))
        self.fetch, self.clock = fetch, clock
        self.checked = {}      # symbol -> (period, latest finest close evaluated)
        self.decided = {}      # symbol -> (period, open, exit time) waiting for its fill price

    def check(self):
        for r in self.runners:
            try:
                self._check(r)
            except Exception as e:     # watching must never stop the trading loop
                print(f"[WATCH ERROR] {r.symbol}: {type(e).__name__}: {e}")

    def _check(self, r):
        if r.last_time is None or not r.exit_watchable():
            return
        period = pd.Timestamp(r.last_time) + self.step
        latest = self.clock().floor(self.fine_step)      # the finest candles closed so far end here
        if latest <= period:
            return
        decided = self.decided.get(r.symbol)
        if decided is None or decided[0] != period:
            self.decided.pop(r.symbol, None)
            if self.checked.get(r.symbol) == (period, latest):
                return
            frames = lower_timeframes.load_signals(r.symbol, self.timeframe, r.signal_fn, period, fetch=self.fetch)
            self.checked[r.symbol] = (period, latest)
            first = frames[self.fine_tf]
            first = first.loc[first["timestamp"] == period, "open"]
            if first.empty:
                return
            t = r.early_exit_time(period, float(first.iloc[0]), frames)
            if t is None:
                return
            decided = self.decided[r.symbol] = (period, float(first.iloc[0]), t)
        _, open_, t = decided
        minute = self.fetch(r.symbol, WATCH_TIMEFRAME, t)
        minute = minute.loc[minute["timestamp"] == t, "open"]
        if minute.empty:
            return                                        # the 1m candle at t hasn't closed yet
        with self.store.candle():
            r.exit_early(period, open_, t, float(minute.iloc[0]))
            self.store.save_state(r.symbol, r.key, r.timeframe, r.engine.to_state(), r.last_time)
        del self.decided[r.symbol]
        print(f"⬇️ [{r.symbol}] lower timeframes {'+'.join(lower_timeframes.LOWER_TIMEFRAMES[self.timeframe])} "
              f"all say SELL at {_fmt(t)} UTC: sold at the {self.fine_tf} open")


def last_price(symbol):
    return float(get_exchange().fetch_ticker(symbol)["last"])


class ManualClose:
    """Sells positions the panel asked to close, at the last traded price (with the usual
    slippage and fee), recorded with reason MANUAL. Afterwards entries wait as after a
    trailing exit, so the next close doesn't simply buy back."""

    def __init__(self, runners, store, key, timeframe, price=last_price, fetch=closed_candles,
                 clock=lambda: pd.Timestamp(now_ms(), unit="ms")):
        self.by_symbol = {r.symbol: r for r in runners}
        self.store, self.key, self.timeframe = store, key, timeframe
        self.step = pd.Timedelta(milliseconds=timeframe_ms(timeframe))
        self.price, self.fetch, self.clock = price, fetch, clock

    def check(self):
        for request_id, symbol in self.store.close_requests(self.key, self.timeframe):
            try:
                self._close(request_id, symbol)
            except Exception as e:     # tried again next minute
                print(f"[CLOSE ERROR] {symbol}: {type(e).__name__}: {e}")

    def _finish(self, request_id, symbol, result):
        with self.store.candle():
            self.store.finish_close_request(request_id, result)
        print(f"✋ [{symbol}] close request: {result}")

    def _close(self, request_id, symbol):
        r = self.by_symbol.get(symbol)
        if r is None or r.last_time is None:
            return self._finish(request_id, symbol, "not traded by this instance")
        e = r.engine
        if e.pending is not None and e.pending[0] == "EXIT" and e.position is not None:
            return self._finish(request_id, symbol, f"already sold at the {self.timeframe} open")
        if not r.exit_watchable():
            return self._finish(request_id, symbol, "no position")
        period = pd.Timestamp(r.last_time) + self.step
        now = self.clock().floor("s")
        if now >= period + self.step:
            return                                        # the candle closed and isn't processed yet: next minute
        open_ = None
        if e.position is None:                            # bought at this candle's open: need that price
            first = self.fetch(symbol, "1m", period)
            first = first.loc[first["timestamp"] == period, "open"]
            if first.empty:
                return
            open_ = float(first.iloc[0])
        price = self.price(symbol)
        with self.store.candle():
            trade = e.exit_inside(period, open_, now, price, reason="MANUAL")
            self.store.log_signal(r.key, r.timeframe, symbol, now, "SELL", None, price, None, "manual close from the panel")
            self.store.save_state(symbol, r.key, r.timeframe, e.to_state(), r.last_time)
            self.store.finish_close_request(request_id, f"sold at {trade.exit_price:.8g}")
        print(f"✋ [{symbol}] closed from the panel at {trade.exit_price:.8g}")


class Watches:
    def __init__(self, watches):
        self.watches = watches

    def check(self):
        for w in self.watches:
            w.check()


def for_instance(inst, runners, store):
    """The intraday watches an instance needs, or None."""
    watches = []
    if inst.manual_close:
        watches.append(ManualClose(runners, store, inst.run_key, inst.timeframe))
    if eligible(inst):
        watches.append(StopWatch(runners, store, inst.run_key, inst.timeframe))
        print("👀 Shadow stop on: open positions are checked against their stop every minute "
              "(recorded only, docs/intrabar-watch.md)\n")
    if inst.lower_tf_exit:
        watches.append(EarlyExitWatch(runners, store, inst.timeframe))
        print(f"👀 Lower-timeframe exit on: {' + '.join(lower_timeframes.LOWER_TIMEFRAMES[inst.timeframe])} "
              "checked after each of their closes\n")
    return Watches(watches) if watches else None
