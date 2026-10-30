# kripto_bot/trading/position_watch.py
"""Intraday watch over a paper instance's positions (docs/intrabar-watch.md).

A 4h or 1d instance only trades at its main candle close. Between closes this module
reads the 1m candles of the main candle that is forming and records the first minute a
stop would have been hit (the shadow stop), with an estimated fill of min(1m open, stop).

It never changes a position: the engine still closes it at the main close, at
min(open, stop) as in the backtest, so paper stays identical to the backtest. The
records show how far a fill seen minute by minute is from that assumption, and how
long after the hit the bot noticed it.
"""
import pandas as pd

from kripto_bot import config
from kripto_bot.core.data import timeframe_ms
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
