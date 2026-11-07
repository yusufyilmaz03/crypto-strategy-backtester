import pandas as pd

from kripto_bot.core import market_context as mc


def _fg(*days):
    """alternative.me response, newest first like the real one."""
    data = [{"value": str(40 + i), "value_classification": "Fear",
             "timestamp": str(int(pd.Timestamp(d, tz="UTC").timestamp()))} for i, d in enumerate(days)]
    return {"data": data[::-1]}


GLOBAL = {"data": {"market_cap_percentage": {"btc": 59.29, "eth": 10.63},
                   "total_market_cap": {"usd": 3.1e12}, "total_volume": {"usd": 1.2e11}}}


class Fake:
    def __init__(self, fg, now):
        self.fg, self.now, self.calls = fg, now, []

    def fetch(self, url):
        self.calls.append(url)
        if "alternative.me" in url:
            return self.fg
        return GLOBAL

    def clock(self):
        return pd.Timestamp(self.now, tz="UTC")


def _ctx(tmp_path, fake):
    return mc.MarketContext(str(tmp_path / "paper.db"), fetch=fake.fetch, clock=fake.clock)


def test_first_run_loads_the_whole_history_then_only_new_days(tmp_path):
    fake = Fake(_fg("2026-11-05", "2026-11-06", "2026-11-07"), "2026-11-07 00:20")
    c = _ctx(tmp_path, fake)
    assert c.update(log=lambda *_: None) == 3 + 4
    assert "limit=0" in fake.calls[0]
    fg = c.frame("fear_greed")
    assert list(fg["date"]) == ["2026-11-05", "2026-11-06", "2026-11-07"]
    assert list(fg["value"]) == [40.0, 41.0, 42.0]

    fake.calls.clear()
    assert c.update(log=lambda *_: None) == 0 and fake.calls == []     # today is complete: no requests

    fake.now, fake.fg = "2026-11-08 00:15", _fg("2026-11-07", "2026-11-08")
    assert c.update(log=lambda *_: None) == 1 + 4
    assert "limit=2" in fake.calls[0]


def test_dominance_keeps_the_first_snapshot_of_the_day(tmp_path):
    fake = Fake(_fg("2026-11-07"), "2026-11-07 00:20")
    c = _ctx(tmp_path, fake)
    c.update(log=lambda *_: None)
    c.conn.execute("DELETE FROM market_context WHERE source='fear_greed'")   # force another round
    c.conn.commit()
    GLOBAL["data"]["market_cap_percentage"]["btc"] = 99.0
    try:
        fake.now = "2026-11-07 09:00"
        c.update(log=lambda *_: None)
    finally:
        GLOBAL["data"]["market_cap_percentage"]["btc"] = 59.29
    dom = c.frame("coingecko_global").set_index("field")
    assert dom.loc["btc_dominance", "value"] == 59.29
    assert dom.loc["btc_dominance", "fetched_at"].startswith("2026-11-07T00:20")
    assert set(dom.index) == {"btc_dominance", "eth_dominance", "total_market_cap_usd", "total_volume_usd"}


def test_a_failing_source_does_not_stop_the_other(tmp_path):
    fake = Fake(None, "2026-11-07 00:20")

    def fetch(url):
        if "alternative.me" in url:
            raise OSError("down")
        return GLOBAL

    c = mc.MarketContext(str(tmp_path / "paper.db"), fetch=fetch, clock=fake.clock)
    logs = []
    assert c.update(log=logs.append) == 4
    assert any("CONTEXT ERROR" in m for m in logs)
    assert c.frame("fear_greed").empty
