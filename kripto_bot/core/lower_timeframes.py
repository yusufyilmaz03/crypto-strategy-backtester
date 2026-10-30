# kripto_bot/core/lower_timeframes.py
"""Lower-timeframe candles for rules that look inside a main candle (docs/intrabar-watch.md).

A 4h or 1d instance trades at its main candle close. Between two closes, the shadow
stop (trading/position_watch.py) reads 1m candles, and the lower-timeframe entry and
exit rules (E and X) read the timeframes in LOWER_TIMEFRAMES. Only closed candles are
returned, so a candle that is still forming can't leak into a decision.
"""
import pandas as pd

from kripto_bot.core.data import drop_unclosed, fetch_range, timeframe_ms, to_datetime_index
from kripto_bot.core.indicators import add_indicators

# Main timeframe -> the lower timeframes its rules look at. Shorter main timeframes
# have none: their lower timeframes are too noisy (docs/intrabar-watch.md).
LOWER_TIMEFRAMES = {"1d": ["4h", "1h"], "4h": ["1h", "15m"]}
WATCH_TIMEFRAME = "1m"     # candles the shadow stop checks
WARMUP = 300               # lower-timeframe candles before the first decision (indicator warm-up)


def has_lower_timeframes(timeframe):
    return timeframe in LOWER_TIMEFRAMES


def closed_candles(symbol, timeframe, since, now=None, exchange=None):
    """Closed candles of `timeframe` with open time >= `since` (a UTC Timestamp), oldest
    first, with a datetime `timestamp` column like the rest of the app."""
    since_ms = int(pd.Timestamp(since).value // 10**6)
    df = fetch_range(symbol, timeframe, since_ms, exchange=exchange)
    return to_datetime_index(drop_unclosed(df, timeframe, now))


def _delta(timeframe):
    return pd.Timedelta(milliseconds=timeframe_ms(timeframe))


def load_signals(symbol, timeframe, signal_fn, since, fetch=closed_candles):
    """Closed candles of each lower timeframe of `timeframe` from WARMUP candles before
    `since`, with the strategy's signal in a 'signal' column: {lower timeframe: frame}."""
    out = {}
    for tf in LOWER_TIMEFRAMES[timeframe]:
        df = fetch(symbol, tf, pd.Timestamp(since) - WARMUP * _delta(tf))
        if df.empty:
            out[tf] = df.assign(signal=pd.Series(dtype=object))
            continue
        df = add_indicators(df)
        out[tf] = df.assign(signal=signal_fn(df).values)
    return out


def signal_at(frame, timeframe, at):
    """Signal of the last `timeframe` candle closed by `at`, or None if that candle is
    missing (no data yet, or a gap)."""
    step = _delta(timeframe)
    last = pd.Timestamp(at).floor(step) - step          # open time of the last closed candle
    rows = frame.loc[frame["timestamp"] == last, "signal"] if len(frame) else []
    return rows.iloc[0] if len(rows) else None


def all_say(frames, at, signal):
    """True if the last closed candle of every lower timeframe says `signal` at `at`."""
    return all(signal_at(df, tf, at) == signal for tf, df in frames.items())


def early_exit_time(frames, period, main_timeframe, stop=None):
    """Rule X: the first close of the finest lower timeframe strictly inside the main
    candle that opened at `period` at which every lower timeframe says SELL, or None.
    The position is sold at the next open of the finest timeframe (that close's time).
    If a finest candle's low reaches the stop first, returns None: the stop exits, as the
    engine does at the main close. Only the candles in `frames` count, so a decision is
    never made on a candle that hasn't closed."""
    fine_tf = LOWER_TIMEFRAMES[main_timeframe][-1]
    fine, fs = frames[fine_tf], _delta(fine_tf)
    end = pd.Timestamp(period) + _delta(main_timeframe)
    inside = fine[(fine["timestamp"] >= period) & (fine["timestamp"] + fs < end)]
    for t, low in zip(inside["timestamp"], inside["low"]):
        if stop is not None and low <= stop:
            return None
        if all_say(frames, t + fs, "SELL"):
            return t + fs
    return None
