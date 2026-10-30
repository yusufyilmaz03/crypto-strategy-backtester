# kripto_bot/core/lower_timeframes.py
"""Lower-timeframe candles for rules that look inside a main candle (docs/intrabar-watch.md).

A 4h or 1d instance trades at its main candle close. Between two closes, the shadow
stop (trading/position_watch.py) reads 1m candles, and the lower-timeframe entry and
exit rules (E and X) read the timeframes in LOWER_TIMEFRAMES. Only closed candles are
returned, so a candle that is still forming can't leak into a decision.

The paper bot (SymbolRunner.step) and the backtest (run_backtest below) take their
decisions through the same functions: early_exit for X and entry_block for E and the
trend filter (docs/v9-filters-test.md).
Lower-timeframe frames are indexed by candle open time, with a 'signal' column.
"""
import pandas as pd

from kripto_bot.core import strategy
from kripto_bot.core.data import drop_unclosed, fetch_range, timeframe_ms, to_datetime_index
from kripto_bot.core.engine import Engine, Trade
from kripto_bot.core.indicators import add_indicators

# Main timeframe -> the lower timeframes its rules look at, coarsest first. Shorter
# main timeframes have none: their lower timeframes are too noisy (docs/intrabar-watch.md).
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


def sma_signal(length):
    """Signal function of the SMA mode: BUY while the close is above SMA(length) of the
    closes, SELL while below. With length 50 it is v9's own signal (fast=1, slow=50)."""
    def signals(df):
        close = df["close"]
        sma = close.rolling(length).mean()
        return strategy._to_signals(df, close > sma, close < sma, warmup=length - 1)
    return signals


def trend_filter(df, length):
    """Trend filter: True where the close is above SMA(length) of the closes, that close
    included (False while there are fewer than `length` closes)."""
    close = df["close"]
    return close > close.rolling(length).mean()


def with_signals(df, signal_fn):
    """Candles -> the same candles indexed by open time, with the strategy's signal."""
    if df.empty:
        return df.assign(signal=pd.Series(dtype=object)).set_index("timestamp", drop=False)
    df = add_indicators(df)
    return df.assign(signal=signal_fn(df).values).set_index("timestamp", drop=False)


def load_signals(symbol, timeframe, signal_fn, since, fetch=closed_candles):
    """Closed candles of each lower timeframe of `timeframe` from WARMUP candles before
    `since`, with the strategy's signal: {lower timeframe: frame}."""
    return {tf: with_signals(fetch(symbol, tf, pd.Timestamp(since) - WARMUP * _delta(tf)), signal_fn)
            for tf in LOWER_TIMEFRAMES[timeframe]}


def signal_at(frame, timeframe, at):
    """Signal of the last `timeframe` candle closed by `at`, or None if that candle is
    missing (no data yet, or a gap)."""
    step = _delta(timeframe)
    last = pd.Timestamp(at).floor(step) - step          # open time of the last closed candle
    return frame["signal"].get(last)


def all_say(frames, at, signal):
    """True if the last closed candle of every lower timeframe says `signal` at `at`."""
    return all(signal_at(df, tf, at) == signal for tf, df in frames.items())


def early_exit_time(frames, period, main_timeframe, stop=None):
    """Rule X: the first close of the finest lower timeframe (the last one in `frames`)
    strictly inside the main candle that opened at `period` at which every lower
    timeframe says SELL, or None. The position is sold at the next open of the finest
    timeframe (that close's time). If a finest candle's low reaches the stop first,
    returns None: the stop exits, as the engine does at the main close. Only the candles
    in `frames` count, so a decision is never made on a candle that hasn't closed."""
    fine_tf = list(frames)[-1]
    fine, fs = frames[fine_tf], _delta(fine_tf)
    end = pd.Timestamp(period) + _delta(main_timeframe)
    inside = fine.loc[pd.Timestamp(period):end - fs - pd.Timedelta(1, "ns")]   # closes before `end`
    for t, low in zip(inside.index, inside["low"]):
        if stop is not None and low <= stop:
            return None
        if all_say(frames, t + fs, "SELL"):
            return t + fs
    return None


def watchable(engine):
    """Is there something rule X could sell in the forming main candle: a long position
    (unless it is sold at the open anyway) or a long entry pending at the open?"""
    if engine.pending is not None and engine.pending[0] == "EXIT":
        return False
    if engine.position is not None:
        return engine.position.side == "LONG"
    return engine.pending is not None and engine.pending[0] == "ENTER" and engine.pending[1] == "LONG"


def exit_time(engine, frames, period, open_, main_timeframe):
    """Rule X for the main candle that opened at `period` (open `open_`), given the
    engine's state before that candle: the time to sell at, or None."""
    if not watchable(engine):
        return None
    stop = engine.position.stop if engine.position is not None else engine.entry_stop(open_)
    return early_exit_time(frames, period, main_timeframe, stop)


def early_exit(engine, frames, period, open_, main_timeframe):
    """Rule X with complete candles (catch-up and backtest): (time, price) to sell at,
    the price being the finest timeframe's open at that time, or None."""
    t = exit_time(engine, frames, period, open_, main_timeframe)
    if t is None:
        return None
    price = frames[list(frames)[-1]]["open"].get(t)
    return None if price is None else (t, float(price))


def entry_block(signal, close_time, frames=None, above_trend=None):
    """Why a BUY at the main close at `close_time` may not queue an entry, or None if it
    may. frames: rule E's lower timeframes (None: E off); above_trend: the trend filter's
    verdict for this close (None: filter off)."""
    if signal != "BUY":
        return None
    if above_trend is not None and not above_trend:
        return "below the trend SMA"
    if frames is not None and not all_say(frames, close_time, "BUY"):
        return "lower timeframes don't confirm"
    return None


def run_backtest(df, signals, frames, config=None, timeframe="1d", entry=False, exit=False, symbol="",
                 trend=None):
    """engine.run_backtest with rules E and/or X and the trend filter, taking each main
    candle the way the paper bot does (SymbolRunner.step). frames: {lower timeframe:
    with_signals(...)}; trend: trend_filter(df, n) or None.
    Returns (trades DataFrame, equity Series indexed by candle time)."""
    engine = Engine(config, symbol)
    step = _delta(timeframe)
    atr = df["ATR"] if "ATR" in df.columns else pd.Series(float("nan"), index=df.index)
    rsi = df["RSI"] if "RSI" in df.columns else pd.Series(float("nan"), index=df.index)
    above = trend if trend is not None else pd.Series(None, index=df.index, dtype=object)
    for t, o, h, lo, c, sig, a, r, up in zip(df["timestamp"], df["open"], df["high"], df["low"], df["close"],
                                             signals, atr, rsi, above):
        if exit:
            x = early_exit(engine, frames, t, o, timeframe)
            if x is not None:
                engine.exit_inside(t, o, *x)
        block = entry_block(sig, t + step, frames if entry else None, None if trend is None else bool(up))
        engine.on_bar(t, o, h, lo, c, sig, a, r, entry_ok=block is None)
    if len(df):
        engine.finish(df["timestamp"].iloc[-1], float(df["close"].iloc[-1]))
    trades = pd.DataFrame([t.__dict__ for t in engine.trades], columns=list(Trade.__dataclass_fields__))
    times, values = zip(*engine.equity_curve) if engine.equity_curve else ((), ())
    equity = pd.Series(values, index=pd.Index(times, name="timestamp"), name="equity", dtype=float)
    return trades, equity
