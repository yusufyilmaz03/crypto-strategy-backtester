# kripto_bot/core/archive.py
"""Daily candles of every Binance spot pair ever listed, from the public archive
(https://data.binance.vision), including pairs that were delisted later.

The exchange API only serves pairs that trade today, so a backtest on today's
symbols silently drops every coin that failed (survivorship bias). The archive
keeps them. Candles are cached per symbol in data/archive/<interval>/ (gzipped below
1d: a 1h history is 24 times larger); a symbol is downloaded again only when a newer
month is available. Monthly files only cover
finished months, so every candle is closed.
"""
import io
import os
import re
import time
import urllib.error
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from kripto_bot import net, paths

BUCKET = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
FILES = "https://data.binance.vision"
COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume",
           "trades", "taker_base", "taker_quote", "ignore"]
ARCHIVE_DIR = os.path.join(paths.DATA_DIR, "archive")


def _get(url, timeout=30, attempts=4):
    for k in range(attempts):
        try:
            with net.urlopen(url, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, OSError):
            if k == attempts - 1:
                raise
            time.sleep(2 ** k)


def _listing(prefix, get=_get):
    """Every key and sub-folder under a prefix of the archive bucket."""
    out, marker = [], ""
    while True:
        q = urllib.parse.urlencode({"delimiter": "/", "prefix": prefix, "marker": marker})
        xml = get(f"{BUCKET}?{q}").decode()
        page = re.findall(r"<Prefix>([^<]+)</Prefix></CommonPrefixes>", xml) + re.findall(r"<Key>([^<]+)</Key>", xml)
        out += page
        if "<IsTruncated>true</IsTruncated>" not in xml or not page:
            return out
        m = re.search(r"<NextMarker>([^<]+)</NextMarker>", xml)
        marker = m.group(1) if m else page[-1]


def list_symbols(quote="USDT", get=_get):
    """All spot pairs with the given quote asset that the archive has, listed or delisted."""
    names = [p.rstrip("/").split("/")[-1] for p in _listing("data/spot/monthly/klines/", get)]
    return sorted(n for n in names if n.endswith(quote) and len(n) > len(quote))


def parse_klines(raw):
    """CSV content of one archive file -> OHLCV frame. Handles optional headers and the
    microsecond timestamps the archive uses from 2025 on."""
    df = pd.read_csv(io.BytesIO(raw), header=None, names=COLUMNS)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    t = df["open_time"].astype("int64")
    t = t.where(t < 10**14, t // 1000)            # microseconds -> milliseconds
    out = pd.DataFrame({"timestamp": pd.to_datetime(t, unit="ms")})
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        out[c] = df[c].astype(float).values
    return out


def _cache_path(symbol, interval):
    return os.path.join(ARCHIVE_DIR, interval, f"{symbol}.csv" + ("" if interval == "1d" else ".gz"))


def _read_cache(path):
    """A cached file. ISO8601 parsing: after Binance's outage of 2018-02-08 the archive's
    candles opened at hh:28:14.789 for two days, so a file can mix timestamps with and
    without milliseconds, which parse_dates leaves as text."""
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601")
    return df


def load_symbol(symbol, interval="1d", refresh=True, get=_get, mapper=map):
    """All archived candles of one pair (cached). Empty frame if the archive has none.
    mapper fetches the monthly files (pass an executor's map to download them in parallel)."""
    path = _cache_path(symbol, interval)
    cached = _read_cache(path) if os.path.exists(path) else None
    if cached is not None and not refresh:
        return cached
    keys = [k for k in _listing(f"data/spot/monthly/klines/{symbol}/{interval}/", get) if k.endswith(".zip")]
    months = sorted(re.search(r"(\d{4}-\d{2})\.zip$", k).group(1) for k in keys)
    if cached is not None and len(cached):
        have = cached["timestamp"].dt.strftime("%Y-%m").unique()
        missing = [m for m in months if m not in have]
        if not missing:
            return cached
    else:
        missing = months
    frames = [cached] if cached is not None and len(cached) else []
    q = urllib.parse.quote(symbol)          # a few tickers are not ASCII
    urls = [f"{FILES}/data/spot/monthly/klines/{q}/{interval}/{q}-{interval}-{m}.zip" for m in missing]
    for raw in mapper(get, urls):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            frames.append(parse_klines(z.read(z.namelist()[0])))
    if not frames:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "quote_volume"])
    df = (pd.concat(frames).drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(f"{path}.tmp", index=False, compression="gzip" if path.endswith(".gz") else None)
    os.replace(f"{path}.tmp", path)       # a killed download never leaves half a file
    return df


def load_all(symbols, interval="1d", refresh=True, workers=8, file_workers=16, log=print):
    """{symbol: frame} for many pairs. Symbols and their monthly files download in
    parallel (one shared pool for the files). Pairs that fail are reported and skipped."""
    out = {}
    files = ThreadPoolExecutor(file_workers)

    def one(s):
        try:
            return s, load_symbol(s, interval, refresh, mapper=files.map)
        except (urllib.error.URLError, OSError, zipfile.BadZipFile, ValueError) as e:
            log(f"⚠️ {s}: {type(e).__name__}: {e}")
            return s, None

    with ThreadPoolExecutor(workers) as ex:
        for k, (s, df) in enumerate(ex.map(one, symbols), 1):
            if df is not None and len(df):
                out[s] = df
            if k % 50 == 0:
                log(f"  {k}/{len(symbols)} symbols")
    files.shutdown()
    return out


def split_listings(df, max_gap_days=5):
    """Split a pair's history where candles are missing for more than max_gap_days.
    A ticker can be reused: Binance's LUNAUSDT holds the old LUNA until May 2022 and
    the new LUNA from June 2022 on. Each piece is treated as its own asset."""
    if df.empty:
        return []
    gaps = df["timestamp"].diff() > pd.Timedelta(days=max_gap_days)
    return [g.reset_index(drop=True) for _, g in df.groupby(gaps.cumsum())]
