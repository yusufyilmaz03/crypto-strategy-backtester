# kripto_bot/core/lower_timeframes.py
"""Lower-timeframe candles for rules that look inside a main candle (docs/intrabar-watch.md).

A 4h or 1d instance trades at its main candle close. Between two closes, the shadow
stop (trading/position_watch.py) reads 1m candles, and the lower-timeframe entry and
exit rules will read the timeframes in LOWER_TIMEFRAMES. Only closed candles are
returned, so a candle that is still forming can't leak into a decision.
"""
import pandas as pd

from kripto_bot.core.data import drop_unclosed, fetch_range, to_datetime_index

# Main timeframe -> the lower timeframes its rules look at. Shorter main timeframes
# have none: their lower timeframes are too noisy (docs/intrabar-watch.md).
LOWER_TIMEFRAMES = {"1d": ["4h", "1h"], "4h": ["1h", "15m"]}
WATCH_TIMEFRAME = "1m"     # candles the shadow stop checks


def has_lower_timeframes(timeframe):
    return timeframe in LOWER_TIMEFRAMES


def closed_candles(symbol, timeframe, since, now=None, exchange=None):
    """Closed candles of `timeframe` with open time >= `since` (a UTC Timestamp), oldest
    first, with a datetime `timestamp` column like the rest of the app."""
    since_ms = int(pd.Timestamp(since).value // 10**6)
    df = fetch_range(symbol, timeframe, since_ms, exchange=exchange)
    return to_datetime_index(drop_unclosed(df, timeframe, now))
