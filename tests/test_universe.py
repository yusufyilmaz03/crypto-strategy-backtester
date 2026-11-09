import pandas as pd

from kripto_bot.core import universe as u

NOW = pd.Timestamp("2026-11-09 08:00", tz="UTC")
TODAY_MS = int(NOW.normalize().timestamp() * 1000)


def _days(n, volume, close=100.0, wiggle=10.0, end_ms=TODAY_MS):
    """n daily candles ending with today's (unclosed) one, oldest first, as Binance sends them."""
    rows = []
    for i in range(n):
        t = end_ms - (n - 1 - i) * u.DAY_MS
        c = close + (wiggle if i % 2 else -wiggle)
        rows.append([t, "0", "0", "0", str(c), "0", t + u.DAY_MS - 1, str(volume), 0, "0", "0", "0"])
    return rows


def _market(base, quote="USDT", spot=True, active=True):
    return {"symbol": f"{base}/{quote}", "id": f"{base}{quote}", "base": base, "quote": quote,
            "spot": spot, "active": active}


class FakeExchange:
    def __init__(self, markets, candles, first=None, fail=()):
        self.markets = {m["symbol"]: m for m in markets}
        self.candles, self.first, self.fail = candles, first or {}, set(fail)
        self.requests = []

    def load_markets(self):
        return self.markets

    def publicGetKlines(self, params):
        self.requests.append(params)
        sym = params["symbol"]
        if sym in self.fail:
            raise RuntimeError("timeout")
        if params.get("startTime") == 0:
            return [[self.first.get(sym, 1_500_000_000_000), "0", "0", "0", "1", "0", 0, "0"]]
        return self.candles[sym][-params["limit"]:]


def _select(markets, candles, n=2, **kw):
    return u.select(n, FakeExchange(markets, candles, **kw), now=NOW, log=lambda *_: None)


def test_ranks_by_the_last_30_closed_days_of_quote_volume():
    candles = {"BTCUSDT": _days(61, 5e9), "ETHUSDT": _days(61, 3e9), "SOLUSDT": _days(61, 4e9)}
    out = _select([_market("BTC"), _market("ETH"), _market("SOL")], candles)
    assert out["symbols"] == ["BTC/USDT", "SOL/USDT"]
    assert [r["rank"] for r in out["ranking"]] == [1, 2, 3]
    assert out["ranking"][0]["quote_volume_30d"] == 30 * 5e9


def test_todays_unclosed_candle_does_not_count():
    rows = _days(61, 1e6)
    rows[-1][7] = str(1e15)                 # a huge volume on today's open candle
    out = _select([_market("BTC")], {"BTCUSDT": rows}, n=1)
    assert out["ranking"][0]["quote_volume_30d"] == 30 * 1e6


def test_only_active_usdt_spot_pairs_are_candidates():
    markets = [_market("BTC"), _market("ETH", quote="BTC"), _market("XRP", spot=False),
               _market("EPIC", active=False)]
    ex = FakeExchange(markets, {"BTCUSDT": _days(61, 1e9)})
    u.select(5, ex, now=NOW, log=lambda *_: None)
    assert {r["symbol"] for r in ex.requests} == {"BTCUSDT"}


def test_the_survivorship_exclusions_apply():
    markets = [_market(b) for b in ("BTC", "USDC", "FDUSD", "EUR", "PAXG", "BTCUP", "BTCDOWN")]
    out = _select(markets, {"BTCUSDT": _days(61, 1e9)}, n=5)
    assert out["symbols"] == ["BTC/USDT"]
    assert len(out["skipped"]) == 6


def test_tokenized_stocks_are_left_out_but_older_coins_ending_in_b_are_kept():
    stock_listed = int(pd.Timestamp("2026-07-01").timestamp() * 1000)
    candles = {"BNBUSDT": _days(61, 2e9), "NVDABUSDT": _days(61, 9e9)}
    out = _select([_market("BNB"), _market("NVDAB")], candles, first={"NVDABUSDT": stock_listed})
    assert out["symbols"] == ["BNB/USDT"]
    assert out["skipped"] == [{"symbol": "NVDAB/USDT", "reason": "tokenized stock or ETF"}]


def test_short_history_missing_yesterday_and_flat_prices_are_left_out():
    candles = {
        "BTCUSDT": _days(61, 1e9),
        "NEWUSDT": _days(40, 9e9),                                         # listed 39 days ago
        "OLDUSDT": _days(61, 9e9, end_ms=TODAY_MS - 3 * u.DAY_MS),         # stopped trading 3 days ago
        "USDXUSDT": _days(61, 9e9, close=1.0, wiggle=0.001),               # an unknown stablecoin
    }
    out = _select([_market(b) for b in ("BTC", "NEW", "OLD", "USDX")], candles, n=5)
    assert out["symbols"] == ["BTC/USDT"]
    reasons = {s["symbol"]: s["reason"] for s in out["skipped"]}
    assert reasons["NEW/USDT"].startswith("fewer than 60")
    assert reasons["OLD/USDT"] == "no candle yesterday"
    assert reasons["USDX/USDT"].startswith("price barely moves")


def test_a_failed_fetch_is_listed_not_hidden():
    candles = {"BTCUSDT": _days(61, 1e9), "ETHUSDT": _days(61, 2e9)}
    out = _select([_market("BTC"), _market("ETH")], candles, fail={"ETHUSDT"})
    assert out["symbols"] == ["BTC/USDT"]
    assert out["skipped"] == [{"symbol": "ETH/USDT", "reason": "fetch failed: timeout"}]
