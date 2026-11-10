# kripto_bot/core/data.py (skill build)
"""OHLCV data access: Binance public klines over HTTPS with a local CSV cache.

Public market data only. This module sends GET requests to the klines endpoint
and nothing else: no API key, no account endpoints, no order endpoints. It
replaces the ccxt-based module of the main repository with the same interface.

Only closed candles are ever returned, so signals can't be computed on a
candle that is still forming.
"""
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from kripto_bot import paths

CACHE_DIR = paths.DATA_DIR
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
BATCH_LIMIT = 1000  # max candles per Binance kline request
# Market-data-only host first; the main API host is the fallback.
HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
KLINES_PATH = "/api/v3/klines"
TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w")
_UNIT_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
_SYMBOL = re.compile(r"^[A-Z0-9]{1,20}/[A-Z0-9]{1,20}$")
PAUSE_SEC = 0.15      # between paginated requests
TIMEOUT_SEC = 30

try:
    import certifi
    _CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:      # fall back to the system store
    _CONTEXT = ssl.create_default_context()


def timeframe_ms(timeframe):
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unsupported timeframe '{timeframe}' (use one of: {', '.join(TIMEFRAMES)})")
    return int(timeframe[:-1]) * _UNIT_MS[timeframe[-1]]


def now_ms():
    return int(time.time() * 1000)


def market_id(symbol):
    """'BTC/USDT' -> 'BTCUSDT'."""
    if not _SYMBOL.match(symbol):
        raise ValueError(f"symbol must look like BTC/USDT, got '{symbol}'")
    return symbol.replace("/", "")


def _get(params):
    """One klines request, trying each host in turn. Returns the parsed JSON rows."""
    query = urllib.parse.urlencode(params)
    error = None
    for host in HOSTS:
        url = f"{host}{KLINES_PATH}?{query}"
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "crypto-strategy-backtester-skill"})
                with urllib.request.urlopen(req, timeout=TIMEOUT_SEC, context=_CONTEXT) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:200]
                if e.code == 400:      # bad symbol or interval: another host won't help
                    raise ValueError(f"Binance rejected the request for {params.get('symbol')}: {body}") from None
                error = f"HTTP {e.code} from {host}: {body}"
                if e.code in (418, 429):
                    wait = min(60.0, float(e.headers.get("Retry-After") or 5))
                    time.sleep(wait)
                    continue
                if e.code >= 500:
                    time.sleep(1 + attempt)
                    continue
                break                  # 403/451 and the like: try the next host
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                error = f"{host}: {e}"
                time.sleep(1 + attempt)
    raise ConnectionError(f"could not download candles ({error})")


def _normalize(df):
    """Integer ms timestamps, sorted, one row per candle."""
    if df.empty:
        return df
    df = df.astype({"timestamp": "int64"})
    return df.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)


def _to_frame(rows):
    """Raw kline rows -> normalized DataFrame (the first six fields of each row)."""
    rows = [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in rows]
    return _normalize(pd.DataFrame(rows, columns=COLUMNS))


def drop_unclosed(df, timeframe, now=None):
    """Drop candles whose close time (open + timeframe) is still in the future."""
    if df.empty:
        return df
    now = now_ms() if now is None else now
    closed = df["timestamp"] + timeframe_ms(timeframe) <= now
    return df[closed].reset_index(drop=True)


def fetch_range(symbol, timeframe, since, until=None):
    """Download candles with open time in [since, until) using paginated requests."""
    step = timeframe_ms(timeframe)
    rows = []
    cursor = since
    while until is None or cursor < until:
        params = {"symbol": market_id(symbol), "interval": timeframe, "startTime": cursor, "limit": BATCH_LIMIT}
        if until is not None:
            params["endTime"] = until - 1
        batch = _get(params)
        if not batch:
            break
        rows.extend(batch)
        next_cursor = int(batch[-1][0]) + step
        if next_cursor <= cursor or len(batch) < BATCH_LIMIT:
            break
        cursor = next_cursor
        time.sleep(PAUSE_SEC)
    df = _to_frame(rows)
    if until is not None and not df.empty:
        df = df[df["timestamp"] < until].reset_index(drop=True)
    return df


def cache_path(symbol, timeframe):
    return os.path.join(CACHE_DIR, f"{market_id(symbol)}_{timeframe}.csv")


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
    """Closed candles for the `days` days before `now` (default: the present), served
    from the cache and topped up from Binance.

    With refresh=False the cache is returned as is (no network). A `now` in the past
    gives a reproducible window; the cache still keeps every closed candle it has.
    Returns a DataFrame with a datetime `timestamp` column plus OHLCV columns.
    """
    real_now = now_ms()
    now = real_now if now is None else min(now, real_now)
    step = timeframe_ms(timeframe)
    start = now - int(days * 86_400_000)
    start -= start % step  # align to candle boundary

    path = cache_path(symbol, timeframe)
    df = _read_cache(path) if use_cache else _to_frame([])

    if refresh:
        parts = [df]
        if df.empty:
            parts.append(fetch_range(symbol, timeframe, start, until=now))
        else:
            first, last = int(df["timestamp"].iloc[0]), int(df["timestamp"].iloc[-1])
            if start < first:
                parts.append(fetch_range(symbol, timeframe, start, until=first))
            if last + step < now:
                parts.append(fetch_range(symbol, timeframe, last + step, until=now))
        frames = [p for p in parts if not p.empty]
        df = _normalize(pd.concat(frames, ignore_index=True)) if frames else _to_frame([])
        df = drop_unclosed(df, timeframe, real_now)
        if use_cache and not df.empty:
            os.makedirs(CACHE_DIR, exist_ok=True)
            df.to_csv(path, index=False)

    # Closed by `now`, and inside the requested window.
    df = df[(df["timestamp"] >= start) & (df["timestamp"] + step <= now)].reset_index(drop=True)
    return to_datetime_index(df)
