import io
import zipfile

import numpy as np
import pandas as pd
import pytest

from kripto_bot.analysis import survivorship as sv
from kripto_bot.core import archive


def frame(start, n, price=100.0, growth=0.0, volume=1.0):
    t = pd.date_range(start, periods=n, freq="D")
    close = price * (1 + growth) ** np.arange(n)
    return pd.DataFrame({"timestamp": t, "open": close, "high": close, "low": close, "close": close,
                         "volume": 1.0, "quote_volume": volume})


def test_parse_klines_handles_headers_and_microseconds():
    ms = int(pd.Timestamp("2024-12-31").value // 10**6)
    us = int(pd.Timestamp("2025-01-01").value // 10**3)
    raw = ("open_time,open,high,low,close,volume,close_time,quote_volume,count,tb,tq,ignore\n"
           f"{ms},1,2,0.5,1.5,10,0,15,1,0,0,0\n{us},1.5,2,1,1.8,10,0,18,1,0,0,0\n").encode()
    df = archive.parse_klines(raw)
    assert list(df.timestamp) == [pd.Timestamp("2024-12-31"), pd.Timestamp("2025-01-01")]
    assert df.close.tolist() == [1.5, 1.8] and df.quote_volume.tolist() == [15, 18]


def test_load_symbol_downloads_missing_months_only(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "ARCHIVE_DIR", str(tmp_path))
    calls = []

    def zipped(month):
        t = int(pd.Timestamp(f"{month}-01").value // 10**6)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("x.csv", f"{t},1,1,1,1,1,0,1,1,0,0,0\n")
        return buf.getvalue()

    listing = {"months": ["2024-01"]}

    def get(url):
        calls.append(url)
        if "?" in url:
            keys = "".join(f"<Key>data/spot/monthly/klines/X/1d/X-1d-{m}.zip</Key>" for m in listing["months"])
            return f"<ListBucketResult>{keys}<IsTruncated>false</IsTruncated></ListBucketResult>".encode()
        return zipped(url[-11:-4])

    assert len(archive.load_symbol("XUSDT", get=get)) == 1
    listing["months"] = ["2024-01", "2024-02"]
    calls.clear()
    assert len(archive.load_symbol("XUSDT", get=get)) == 2
    assert sum(u.endswith(".zip") for u in calls) == 1          # only the new month


def test_intraday_archive_is_gzipped(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "ARCHIVE_DIR", str(tmp_path))
    t = int(pd.Timestamp("2024-01-01").value // 10**6)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.csv", "".join(f"{t + k * 3_600_000},1,2,0.5,1.5,1,0,1,1,0,0,0\n" for k in range(3)))

    def get(url):
        if "?" in url:
            return (b"<ListBucketResult><Key>data/spot/monthly/klines/X/1h/X-1h-2024-01.zip</Key>"
                    b"<IsTruncated>false</IsTruncated></ListBucketResult>")
        return buf.getvalue()

    df = archive.load_symbol("XUSDT", "1h", get=get)
    path = tmp_path / "1h" / "XUSDT.csv.gz"
    assert path.exists() and path.read_bytes()[:2] == b"\x1f\x8b"          # gzip magic
    cached = archive.load_symbol("XUSDT", "1h", refresh=False)
    assert len(df) == len(cached) == 3 and cached.timestamp.iloc[1] == pd.Timestamp("2024-01-01 01:00")


def test_reused_ticker_is_split_and_stablecoins_dropped():
    old = frame("2022-01-01", 30)
    new = frame("2022-03-01", 30, price=5)
    frames = {"LUNAUSDT": pd.concat([old, new], ignore_index=True), "USDCUSDT": frame("2022-01-01", 30),
              "BTCUSDT": frame("2022-01-01", 30), "BTCUPUSDT": frame("2022-01-01", 30),
              "JUPUSDT": frame("2022-01-01", 30), "BULLUSDT": frame("2020-01-17", 30),
              "NVDABUSDT": frame("2026-06-11", 30), "BNBUSDT": frame("2017-11-06", 30)}
    assets = sv.build_assets(frames)
    assert set(assets) == {"LUNAUSDT", "LUNAUSDT#2", "BTCUSDT", "JUPUSDT", "BNBUSDT"}
    assert assets["LUNAUSDT#2"].close.iloc[0] == 5


def test_universe_is_point_in_time():
    assets = {"BIG": frame("2020-01-01", 200, volume=10), "SMALL": frame("2020-01-01", 200, volume=1),
              "NEW": frame("2020-04-01", 100, volume=100),        # too little history at first
              "DEAD": frame("2020-01-01", 80, volume=50)}         # last candle 2020-03-20
    months = sv.month_starts("2020-03-01", "2020-07-01")
    u = sv.universe(assets, months, n=2, min_history=60)
    assert u[pd.Timestamp("2020-03-01")] == ["DEAD", "BIG"]
    assert u[pd.Timestamp("2020-04-01")] == ["BIG", "SMALL"]       # DEAD stopped trading
    assert u[pd.Timestamp("2020-06-01")] == ["NEW", "BIG"]         # NEW has 61 days by June
    # what happens on or after the month start can't change that month's universe
    changed = dict(assets, SMALL=assets["SMALL"].assign(
        quote_volume=np.where(assets["SMALL"].timestamp >= "2020-04-01", 1e9, 1)))
    assert sv.universe(changed, months, n=2, min_history=60)[pd.Timestamp("2020-04-01")] == ["BIG", "SMALL"]


def test_portfolio_costs_and_delisting():
    days = pd.date_range("2020-01-01", "2020-03-31", freq="D")
    a = pd.Series(np.linspace(1, 2, len(days)), index=days)      # grows steadily
    b_days = days[days <= "2020-02-15"]
    b = pd.Series(1.0, index=b_days)                             # flat, delisted mid-February
    sleeves = {"A": (a, pd.Series(True, index=days), pd.DatetimeIndex([])),
               "B": (b, pd.Series(True, index=b_days), pd.DatetimeIndex([]))}
    members = {pd.Timestamp("2020-02-01"): ["A", "B"], pd.Timestamp("2020-03-01"): ["A"]}
    c = 0.01
    values, expo, _ = sv.portfolio(sleeves, members, "2020-04-01", c)
    jan31, feb29 = pd.Timestamp("2020-01-31"), pd.Timestamp("2020-02-29")
    feb = 0.5 * (a[feb29] / a[jan31] * (1 - c)) + 0.5 * (1 - c) * (1 - c)   # B: bought, sold at delisting
    assert values[feb29] == pytest.approx(feb)
    assert values.iloc[-1] == pytest.approx(feb * a.iloc[-1] / a[feb29])   # A stays: no new costs
    assert values.index[0] == jan31 and values.iloc[0] == 1.0
    assert 0.5 < expo < 1


def test_flat_strategy_sleeve_never_pays_join_costs():
    days = pd.date_range("2020-01-01", "2020-02-29", freq="D")
    v = pd.Series(1.0, index=days)
    sleeves = {"A": (v, pd.Series(False, index=days), pd.DatetimeIndex([]))}
    values, expo, _ = sv.portfolio(sleeves, {pd.Timestamp("2020-02-01"): ["A"]}, "2020-03-01", 0.01)
    assert values.iloc[-1] == 1.0 and expo == 0


def test_strategy_sleeve_matches_engine_positions(tmp_path):
    from kripto_bot.core.engine import EngineConfig
    df = frame("2020-01-01", 120)
    df["close"] = df["open"] = df["high"] = df["low"] = np.r_[np.full(60, 100.0), np.linspace(100, 160, 60)]
    eq, pos, entries = sv.strategy_sleeve(df, "v9", {"fast": 1, "slow": 50}, EngineConfig(0, 0, None, False, 1000))
    assert not pos.iloc[:60].any() and pos.iloc[-2]
    assert len(entries) == 1 and eq.iloc[-1] > 1000


def test_bootstrap_of_identical_series_is_zero():
    r = np.random.default_rng(1).normal(0.001, 0.02, 400)
    d = sv.bootstrap_sharpe_diff(r, r, n=50)
    assert np.allclose(d, 0)


def test_portfolio_weights():
    days = pd.date_range("2020-01-01", "2020-02-29", freq="D")
    a = pd.Series(np.linspace(1, 2, len(days)), index=days)
    b = pd.Series(1.0, index=days)
    sleeves = {"A": (a, pd.Series(False, index=days), pd.DatetimeIndex([])),
               "B": (b, pd.Series(False, index=days), pd.DatetimeIndex([]))}
    m = pd.Timestamp("2020-02-01")
    values, _, _ = sv.portfolio(sleeves, {m: ["A", "B"]}, "2020-03-01", 0.0, weights={m: {"A": 3, "B": 1}})
    jan31 = pd.Timestamp("2020-01-31")
    assert values.iloc[-1] == pytest.approx(0.75 * a.iloc[-1] / a[jan31] + 0.25)


def test_inverse_vol_weights_use_only_past_data():
    rng = np.random.default_rng(0)
    t = pd.date_range("2020-01-01", "2020-04-30", freq="D")
    calm = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(t))))
    wild = 100 * np.exp(np.cumsum(rng.normal(0, 0.04, len(t))))
    assets = {"CALM": pd.DataFrame({"timestamp": t, "close": calm}),
              "WILD": pd.DataFrame({"timestamp": t, "close": wild})}
    m = pd.Timestamp("2020-04-01")
    w = sv.inverse_vol_weights(assets, {m: ["CALM", "WILD"]}, lookback=60)[m]
    assert 3 < w["CALM"] / w["WILD"] < 5.5
    later = dict(assets, WILD=assets["WILD"].assign(close=np.where(t >= m, wild * 50, wild)))
    assert sv.inverse_vol_weights(later, {m: ["CALM", "WILD"]}, lookback=60)[m] == w
