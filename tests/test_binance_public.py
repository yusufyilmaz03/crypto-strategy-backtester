import io
import json
import urllib.error
import urllib.parse

import pytest

from kripto_bot.core import binance_public as bp
from kripto_bot.core import data


def _info(symbol, base, quote="USDT", status="TRADING", notional="NOTIONAL"):
    return {"symbol": symbol, "status": status, "baseAsset": base, "quoteAsset": quote, "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
        {"filterType": "LOT_SIZE", "stepSize": "0.00001000"},
        {"filterType": notional, "minNotional": "5.00000000"}]}


INFO = {"BTCUSDT": _info("BTCUSDT", "BTC"), "ETHUSDT": _info("ETHUSDT", "ETH", notional="MIN_NOTIONAL"),
        "OLDUSDT": _info("OLDUSDT", "OLD", status="BREAK")}


class FakeBinance:
    """Answers like api.binance.com/api/v3 and records every request."""

    def __init__(self):
        self.requests = []

    def __call__(self, url, timeout=None):
        path, _, query = url.removeprefix(bp.BASE_URL).partition("?")
        params = dict(urllib.parse.parse_qsl(query))
        self.requests.append((path, params))
        if path == "klines":
            rows = [[1000 * i, "1.5", "2", "1", "1.75", "10.0", 1000 * i + 999, "17.5", 3, "0", "0", "0"]
                    for i in range(int(params.get("limit", 3)))]
            return io.StringIO(json.dumps(rows))
        if path == "ticker/price":
            if "symbol" in params:                       # one symbol: one object, not a list
                return io.StringIO(json.dumps({"symbol": params["symbol"], "price": "101.5"}))
            return io.StringIO(json.dumps([{"symbol": i, "price": "101.5"} for i in json.loads(params["symbols"])]))
        if path == "exchangeInfo":
            ids = json.loads(params["symbols"]) if "symbols" in params else \
                [params["symbol"]] if "symbol" in params else list(INFO)
            if len(set(ids)) < len(ids):
                raise urllib.error.HTTPError(url, 400, "Bad Request", {}, io.BytesIO(
                    b'{"code":-1102,"msg":"Symbol is present multiple times in the list."}'))
            if any(i not in INFO for i in ids):
                raise urllib.error.HTTPError(url, 400, "Bad Request", {},
                                             io.BytesIO(b'{"code":-1121,"msg":"Invalid symbol."}'))
            return io.StringIO(json.dumps({"symbols": [INFO[i] for i in ids]}))
        raise AssertionError(path)


@pytest.fixture
def fake():
    return FakeBinance()


@pytest.fixture
def client(fake):
    return bp.BinancePublic(urlopen=fake, sleep=lambda s: None)


def test_klines_in_ccxt_shape(client, fake):
    rows = client.fetch_ohlcv("BTC/USDT", "1d", since=5000, limit=2)
    assert rows == [[0, 1.5, 2.0, 1.0, 1.75, 10.0], [1000, 1.5, 2.0, 1.0, 1.75, 10.0]]
    assert fake.requests == [("klines", {"symbol": "BTCUSDT", "interval": "1d", "startTime": "5000", "limit": "2"})]


def test_no_start_time_when_none(client, fake):
    client.fetch_ohlcv("BTC/USDT", "1m", limit=3)
    assert "startTime" not in fake.requests[0][1]


def test_raw_klines_keep_the_quote_volume(client):
    assert client.publicGetKlines({"symbol": "BTCUSDT", "interval": "1d", "limit": 1})[0][7] == "17.5"


def test_tickers_in_one_request(client, fake):
    assert client.fetch_ticker("ETH/USDT") == {"symbol": "ETH/USDT", "last": 101.5}
    out = client.fetch_tickers(["BTC/USDT", "ETH/USDT"])
    assert out == {"BTC/USDT": {"symbol": "BTC/USDT", "last": 101.5}, "ETH/USDT": {"symbol": "ETH/USDT", "last": 101.5}}
    assert fake.requests[-1] == ("ticker/price", {"symbols": '["BTCUSDT","ETHUSDT"]'})


def test_markets_only_for_the_symbols_asked_and_cached(client, fake):
    m = client.load_markets(["BTC/USDT", "ETH/USDT"])
    assert m["BTC/USDT"] == {"symbol": "BTC/USDT", "id": "BTCUSDT", "base": "BTC", "quote": "USDT", "spot": True,
                             "active": True, "precision": {"amount": 1e-05, "price": 0.01},
                             "limits": {"cost": {"min": 5.0}}}
    assert m["ETH/USDT"]["limits"]["cost"]["min"] == 5.0           # older MIN_NOTIONAL filter
    assert client.load_markets(["BTC/USDT"]) is m and len(fake.requests) == 1


def test_all_markets_once(client, fake):
    m = client.load_markets()
    assert sorted(m) == ["BTC/USDT", "ETH/USDT", "OLD/USDT"] and not m["OLD/USDT"]["active"]
    client.load_markets()
    client.load_markets(["BTC/USDT"])
    assert len(fake.requests) == 1


def test_repeated_symbols_are_asked_once(client, fake):
    # v11 asks for its symbols + BTC/USDT, which may already be among them
    assert sorted(client.load_markets(["BTC/USDT", "ETH/USDT", "BTC/USDT"])) == ["BTC/USDT", "ETH/USDT"]
    assert fake.requests == [("exchangeInfo", {"symbols": '["BTCUSDT","ETHUSDT"]'})]


def test_unknown_symbol_is_left_out(client):
    assert sorted(client.load_markets(["BTC/USDT", "GONE/USDT"])) == ["BTC/USDT"]


def test_other_errors_are_raised_with_binance_message(fake):
    def fail(url, timeout=None):
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {},
                                     io.BytesIO(b'{"code":-1003,"msg":"Too many requests."}'))
    client = bp.BinancePublic(urlopen=fail, sleep=lambda s: None)
    with pytest.raises(bp.BinanceError, match="429 Too many requests") as e:
        client.load_markets(["BTC/USDT"])
    assert e.value.code == -1003


def test_requests_are_spaced(fake):
    now, waits = [0.0], []
    client = bp.BinancePublic(urlopen=fake, clock=lambda: now[0], sleep=waits.append)
    client.fetch_ticker("BTC/USDT")
    now[0] += 0.01
    client.fetch_ticker("BTC/USDT")
    assert waits == [pytest.approx(bp.MIN_INTERVAL_S - 0.01)]


@pytest.mark.parametrize("tf, ms", [("1m", 60_000), ("15m", 900_000), ("4h", 14_400_000), ("1d", 86_400_000),
                                    ("1w", 604_800_000), ("1M", 2_592_000_000)])
def test_timeframe_ms(tf, ms):
    assert data.timeframe_ms(tf) == ms
