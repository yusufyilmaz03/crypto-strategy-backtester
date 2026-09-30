import pandas as pd

import data
from conftest import MINUTE, FakeExchange

T0 = 1_700_000_000_000 - (1_700_000_000_000 % MINUTE)


def test_drop_unclosed_removes_forming_candle():
    df = pd.DataFrame({"timestamp": [T0, T0 + MINUTE, T0 + 2 * MINUTE], "open": 1, "high": 1,
                       "low": 1, "close": 1, "volume": 1})
    # At T0 + 2.5 min the candle opened at T0 + 2 min is still forming.
    out = data.drop_unclosed(df, "1m", now=T0 + 2 * MINUTE + 30_000)
    assert out["timestamp"].tolist() == [T0, T0 + MINUTE]
    # Exactly at its close time it counts as closed.
    assert len(data.drop_unclosed(df, "1m", now=T0 + 3 * MINUTE)) == 3


def test_fetch_range_paginates_without_gaps_or_duplicates(monkeypatch):
    monkeypatch.setattr(data, "BATCH_LIMIT", 100)
    ex = FakeExchange(first=T0, last=T0 + 349 * MINUTE)
    df = data.fetch_range("X/USDT", "1m", T0, exchange=ex)
    assert len(df) == 350
    assert df["timestamp"].diff().dropna().eq(MINUTE).all()
    assert len(ex.calls) == 4


def test_load_ohlcv_uses_cache_and_only_fetches_new_candles(tmp_cache):
    now = T0 + 300 * MINUTE + 30_000  # candle at T0+300min is still forming
    ex = FakeExchange(first=T0 - 10_000 * MINUTE, last=T0 + 300 * MINUTE)
    df = data.load_ohlcv("X/USDT", "1m", days=0.1, exchange=ex, now=now)

    assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])
    last_open = int(df["timestamp"].iloc[-1].value // 10**6)
    assert last_open == T0 + 299 * MINUTE  # forming candle dropped
    assert len(df) == 144  # 0.1 day of 1m candles

    # Second call one hour later: only the tail is requested.
    ex2 = FakeExchange(first=T0 - 10_000 * MINUTE, last=T0 + 360 * MINUTE)
    df2 = data.load_ohlcv("X/USDT", "1m", days=0.1, exchange=ex2, now=now + 60 * MINUTE)
    assert ex2.calls == [T0 + 300 * MINUTE]
    assert int(df2["timestamp"].iloc[-1].value // 10**6) == T0 + 359 * MINUTE


def test_load_ohlcv_extends_cache_backwards(tmp_cache):
    now = T0 + 300 * MINUTE
    ex = FakeExchange(first=T0 - 10_000 * MINUTE, last=T0 + 299 * MINUTE)
    data.load_ohlcv("X/USDT", "1m", days=0.05, exchange=ex, now=now)
    longer = data.load_ohlcv("X/USDT", "1m", days=0.2, exchange=ex, now=now)
    assert len(longer) == 288
    assert longer["timestamp"].diff().dropna().eq(pd.Timedelta(minutes=1)).all()


def test_fetch_recent_drops_forming_candle():
    ex = FakeExchange(first=T0, last=T0 + 50 * MINUTE)
    df = data.fetch_recent("X/USDT", "1m", limit=10, exchange=ex, now=T0 + 50 * MINUTE + 1)
    assert len(df) == 9


def test_load_ohlcv_without_refresh_reads_cache_only(tmp_cache):
    now = T0 + 300 * MINUTE
    ex = FakeExchange(first=T0 - 10_000 * MINUTE, last=T0 + 299 * MINUTE)
    data.load_ohlcv("X/USDT", "1m", days=0.1, exchange=ex, now=now)
    calls = len(ex.calls)
    df = data.load_ohlcv("X/USDT", "1m", days=0.1, exchange=ex, now=now + 60 * MINUTE, refresh=False)
    assert len(ex.calls) == calls           # no network
    assert len(df) == 144 - 60              # window moved on, cache not topped up
