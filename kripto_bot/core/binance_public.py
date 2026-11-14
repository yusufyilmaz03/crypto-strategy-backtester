# kripto_bot/core/binance_public.py
"""Binance spot market data over the public REST API, without ccxt.

ccxt loads every Binance market (spot, futures and options, about 4,700; the spot
exchangeInfo alone is an 18 MB answer) on its first request and keeps them. With the
ccxt import that made each paper instance process about 234 MB (TODO.md, "Server").
The bots only need klines, last prices and the rules of their own symbols, so this
client asks Binance for just those.

The methods are named and shaped like the ccxt ones they replace (fetch_ohlcv,
fetch_ticker, fetch_tickers, load_markets, publicGetKlines), so callers and the test
fakes stay as they are. Order sending stays on ccxt (trading/broker.py).
"""
import json
import threading
import time
import urllib.error
import urllib.parse

from kripto_bot import net

BASE_URL = "https://api.binance.com/api/v3/"
MIN_INTERVAL_S = 0.05     # between requests of one process (ccxt's rate limit for Binance)
TIMEOUT_S = 10            # ccxt's default


INVALID_SYMBOL = -1121


class BinanceError(Exception):
    """Binance answered with an error (unknown symbol, rate limit, ...); `code` is Binance's."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def market_id(symbol):
    """"BTC/USDT" -> "BTCUSDT"."""
    return symbol.replace("/", "")


def _json_list(items):
    return json.dumps(list(items), separators=(",", ":"))


def _filter(info, kind):
    return next((f for f in info.get("filters", []) if f.get("filterType") == kind), {})


def _float(value):
    return float(value) if value not in (None, "") else None


def parse_market(info):
    """One exchangeInfo symbol -> the ccxt market fields the bot reads."""
    notional = _filter(info, "NOTIONAL") or _filter(info, "MIN_NOTIONAL")
    base, quote = info["baseAsset"], info["quoteAsset"]
    return {
        "symbol": f"{base}/{quote}", "id": info["symbol"], "base": base, "quote": quote,
        "spot": True, "active": info.get("status") == "TRADING",
        "precision": {"amount": _float(_filter(info, "LOT_SIZE").get("stepSize")),
                      "price": _float(_filter(info, "PRICE_FILTER").get("tickSize"))},
        "limits": {"cost": {"min": _float(notional.get("minNotional"))}},
    }


class BinancePublic:
    def __init__(self, base_url=BASE_URL, urlopen=net.urlopen, clock=time.monotonic, sleep=time.sleep):
        self.base_url, self.urlopen, self.clock, self.sleep = base_url, urlopen, clock, sleep
        self.markets = {}
        self._all_markets = False
        self._lock = threading.Lock()
        self._last = None

    def _get(self, path, params=None):
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = self.base_url + path + ("?" + query if query else "")
        with self._lock:
            if self._last is not None:
                wait = self._last + MIN_INTERVAL_S - self.clock()
                if wait > 0:
                    self.sleep(wait)
            self._last = self.clock()
        try:
            with self.urlopen(url, timeout=TIMEOUT_S) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                body = json.load(e)
            except (ValueError, OSError):
                body = {}
            body = body if isinstance(body, dict) else {}
            raise BinanceError(f"{path} {params or ''}: HTTP {e.code} {body.get('msg') or e.reason}",
                               body.get("code")) from None

    def publicGetKlines(self, params):
        """Raw klines, every field as Binance sends it (the quote volume is field 7)."""
        return self._get("klines", params)

    def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None):
        """[[open time ms, open, high, low, close, base volume], ...], oldest first."""
        rows = self.publicGetKlines({"symbol": market_id(symbol), "interval": timeframe,
                                     "startTime": since, "limit": limit})
        return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in rows]

    def fetch_ticker(self, symbol):
        r = self._get("ticker/price", {"symbol": market_id(symbol)})
        return {"symbol": symbol, "last": float(r["price"])}

    def fetch_tickers(self, symbols):
        """{symbol: {"symbol", "last"}} for the given symbols, in one request."""
        by_id = {market_id(s): s for s in symbols}
        rows = self._get("ticker/price", {"symbols": _json_list(by_id)})
        return {by_id[r["symbol"]]: {"symbol": by_id[r["symbol"]], "last": float(r["price"])}
                for r in rows if r["symbol"] in by_id}

    def _exchange_info(self, params=None):
        for info in self._get("exchangeInfo", params)["symbols"]:
            m = parse_market(info)
            self.markets[m["symbol"]] = m

    def load_markets(self, symbols=None):
        """The markets of `symbols` (every spot market if None), kept like ccxt keeps them.
        Unknown symbols are left out, as ccxt has no market for them either."""
        if symbols is None:
            if not self._all_markets:
                self._exchange_info()
                self._all_markets = True
            return self.markets
        missing = list(dict.fromkeys(market_id(s) for s in symbols if s not in self.markets))  # Binance refuses repeats
        if missing and not self._all_markets:
            try:
                self._exchange_info({"symbols": _json_list(missing)})
            except BinanceError as e:     # one unknown symbol fails the whole request
                if e.code != INVALID_SYMBOL:
                    raise
                for mid in missing:
                    try:
                        self._exchange_info({"symbol": mid})
                    except BinanceError as e:
                        if e.code != INVALID_SYMBOL:
                            raise
        return self.markets
