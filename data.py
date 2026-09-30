# data.py
"""OHLCV data access: paginated downloads from Binance with a local CSV cache.

Only closed candles are ever returned, so signals can't be computed on a
candle that is still forming.
"""
import os
import time

import ccxt
import pandas as pd

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
BATCH_LIMIT = 1000  # max candles per Binance kline request

_exchange = None


def get_exchange():
    global _exchange
    if _exchange is None:
        _exchange = ccxt.binance({"enableRateLimit": True})
    return _exchange


def timeframe_ms(timeframe):
    return ccxt.Exchange.parse_timeframe(timeframe) * 1000


def now_ms():
    return int(time.time() * 1000)


def _normalize(df):
    """Integer ms timestamps, sorted, one row per candle."""
    if df.empty:
        return df
    df = df.astype({"timestamp": "int64"})
    return df.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)


def _to_frame(rows):
    """Raw ccxt rows -> normalized DataFrame."""
    return _normalize(pd.DataFrame(rows, columns=COLUMNS))


def drop_unclosed(df, timeframe, now=None):
    """Drop candles whose close time (open + timeframe) is still in the future."""
    if df.empty:
        return df
    now = now_ms() if now is None else now
    closed = df["timestamp"] + timeframe_ms(timeframe) <= now
    return df[closed].reset_index(drop=True)


def fetch_range(symbol, timeframe, since, until=None, exchange=None):
    """Download candles with open time in [since, until) using paginated requests."""
    ex = exchange or get_exchange()
    step = timeframe_ms(timeframe)
    rows = []
    cursor = since
    while until is None or cursor < until:
        batch = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=BATCH_LIMIT)
        if not batch:
            break
        rows.extend(batch)
        next_cursor = batch[-1][0] + step
        if next_cursor <= cursor or len(batch) < BATCH_LIMIT:
            break
        cursor = next_cursor
    df = _to_frame(rows)
    if until is not None and not df.empty:
        df = df[df["timestamp"] < until].reset_index(drop=True)
    return df


def cache_path(symbol, timeframe):
    return os.path.join(CACHE_DIR, f"{symbol.replace('/', '_')}_{timeframe}.csv")


def _read_cache(path):
    if not os.path.exists(path):
        return _to_frame([])
    return _normalize(pd.read_csv(path))


def to_datetime_index(df):
    """Return a copy with `timestamp` converted from ms to datetime (UTC, naive)."""
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], unit="ms")
    return out


def load_ohlcv(symbol, timeframe, days, exchange=None, now=None, use_cache=True, refresh=True):
    """Closed candles for the last `days` days, served from the cache and topped up from Binance.

    With refresh=False the cache is returned as is (no network), e.g. for parallel
    workers after the data has been prefetched.
    Returns a DataFrame with a datetime `timestamp` column plus OHLCV columns.
    """
    now = now_ms() if now is None else now
    step = timeframe_ms(timeframe)
    start = now - int(days * 86_400_000)
    start -= start % step  # align to candle boundary

    path = cache_path(symbol, timeframe)
    cached = _read_cache(path) if use_cache else _to_frame([])

    if not refresh:
        return to_datetime_index(cached[cached["timestamp"] >= start].reset_index(drop=True))

    parts = [cached]
    if cached.empty:
        parts.append(fetch_range(symbol, timeframe, start, exchange=exchange))
    else:
        first, last = int(cached["timestamp"].iloc[0]), int(cached["timestamp"].iloc[-1])
        if start < first:
            parts.append(fetch_range(symbol, timeframe, start, until=first, exchange=exchange))
        parts.append(fetch_range(symbol, timeframe, last + step, exchange=exchange))

    frames = [p for p in parts if not p.empty]
    df = _normalize(pd.concat(frames, ignore_index=True)) if frames else _to_frame([])
    df = drop_unclosed(df, timeframe, now)

    if use_cache and not df.empty:
        os.makedirs(CACHE_DIR, exist_ok=True)
        df.to_csv(path, index=False)

    df = df[df["timestamp"] >= start].reset_index(drop=True)
    return to_datetime_index(df)


def fetch_recent(symbol, timeframe, limit=200, exchange=None, now=None):
    """Latest closed candles without touching the cache (for the realtime loop)."""
    ex = exchange or get_exchange()
    rows = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = drop_unclosed(_to_frame(rows), timeframe, now)
    return to_datetime_index(df)
