# kripto_bot/trading/broker.py
"""Order execution on Binance spot (testnet by default) plus a fake broker for tests.

Only market orders are used: a strategy decision made at a candle close is sent right
away, which corresponds to the backtest's "fill at the next open". Every order carries
a deterministic client order id, so after a crash the same decision can be looked up
on the exchange instead of being sent twice.
"""
import math
import time
from dataclasses import dataclass

import ccxt

from kripto_bot import config


class LiveTradingDisabled(RuntimeError):
    pass


@dataclass
class Fill:
    client_id: str
    order_id: str
    symbol: str
    side: str            # "buy" | "sell"
    status: str          # ccxt status: "closed", "open", "canceled", ...
    filled: float        # base amount
    cost: float          # quote amount
    average: float       # average fill price
    fee_quote: float     # fees converted to quote currency (approximate for non-quote fees)
    fee_base: float = 0.0  # part of the fee paid in the base asset (Binance takes buy fees from it)

    @property
    def net_base(self):
        """Base amount actually received (buys) or given up (sells)."""
        return self.filled - self.fee_base if self.side == "buy" else self.filled


def client_order_id(symbol, candle_time, side):
    """Deterministic id per decision (Binance allows up to 36 chars: [.A-Z:/a-z0-9_-])."""
    ts = int(candle_time.timestamp()) if hasattr(candle_time, "timestamp") else int(candle_time)
    base = symbol.replace("/", "")[:14]
    return f"kb-{base}-{ts}-{side[0]}"


def _retry(fn, attempts=3, delay_s=2.0):
    """Retry transient network errors; anything else is raised immediately."""
    for i in range(attempts):
        try:
            return fn()
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout):
            if i == attempts - 1:
                raise
            time.sleep(delay_s * (i + 1))


def _fee_in_base(order, base):
    fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
    return sum(float(f["cost"]) for f in fees if f and f.get("currency") == base and f.get("cost"))


def _fee_in_quote(order, quote):
    total = 0.0
    for fee in order.get("fees") or ([order["fee"]] if order.get("fee") else []):
        if not fee or fee.get("cost") is None:
            continue
        if fee.get("currency") == quote:
            total += float(fee["cost"])
        elif order.get("average"):
            # fee paid in the base asset (or BNB, approximated at the fill price)
            total += float(fee["cost"]) * float(order["average"])
    return total


class CcxtBroker:
    def __init__(self, mode=None, api_key=None, secret=None):
        mode = mode or config.TRADING_MODE
        if mode == "live":
            if not config.LIVE_TRADING_ENABLED:
                raise LiveTradingDisabled(
                    "Live trading is disabled. It requires LIVE_TRADING_ENABLED = True in config.py.")
            api_key = api_key or config.BINANCE_API_KEY
            secret = secret or config.BINANCE_API_SECRET
        elif mode == "testnet":
            api_key = api_key or config.BINANCE_TESTNET_API_KEY
            secret = secret or config.BINANCE_TESTNET_API_SECRET
        else:
            raise ValueError(f"Unknown trading mode: {mode}")
        if not api_key or not secret:
            raise RuntimeError(f"Missing API keys for {mode} (see .env.example).")
        self.mode = mode
        self.ex = ccxt.binance({"apiKey": api_key, "secret": secret, "enableRateLimit": True,
                                "options": {"defaultType": "spot"}})
        if mode == "testnet":
            self.ex.set_sandbox_mode(True)
        self.markets = _retry(self.ex.load_markets)

    # ---------- market rules ----------
    def min_cost(self, symbol):
        return float(self.markets[symbol]["limits"]["cost"]["min"] or 0)

    def round_amount(self, symbol, amount):
        """Round a base amount down to the symbol's lot step."""
        return float(self.ex.amount_to_precision(symbol, amount))

    # ---------- account ----------
    def free_balance(self, asset):
        bal = _retry(self.ex.fetch_balance)
        return float(bal.get(asset, {}).get("free") or 0)

    # ---------- orders ----------
    def _to_fill(self, order, symbol, client_id):
        m = self.markets[symbol]
        return Fill(client_id=client_id, order_id=str(order.get("id")), symbol=symbol,
                    side=order.get("side"), status=order.get("status"),
                    filled=float(order.get("filled") or 0), cost=float(order.get("cost") or 0),
                    average=float(order.get("average") or 0),
                    fee_quote=_fee_in_quote(order, m["quote"]), fee_base=_fee_in_base(order, m["base"]))

    def find_order(self, symbol, client_id):
        """The order sent earlier under this client id, or None."""
        try:
            order = _retry(lambda: self.ex.fetch_order(None, symbol, {"origClientOrderId": client_id}))
        except ccxt.OrderNotFound:
            return None
        return self._to_fill(order, symbol, client_id)

    def market_buy(self, symbol, quote_amount, client_id):
        order = _retry(lambda: self.ex.create_market_buy_order_with_cost(
            symbol, quote_amount, {"newClientOrderId": client_id}))
        return self._to_fill(self._settled(order, symbol), symbol, client_id)

    def market_sell(self, symbol, amount, client_id):
        order = _retry(lambda: self.ex.create_order(
            symbol, "market", "sell", amount, None, {"newClientOrderId": client_id}))
        return self._to_fill(self._settled(order, symbol), symbol, client_id)

    def _settled(self, order, symbol):
        """Market orders fill immediately; re-fetch if the response lacks fill details."""
        if order.get("status") == "closed" and order.get("filled"):
            return order
        return _retry(lambda: self.ex.fetch_order(order["id"], symbol))


class FakeBroker:
    """In-memory broker for tests: fills at a given price with lot/min-notional rules.
    Like Binance, buy fees are taken from the base asset and sell fees from the quote."""

    def __init__(self, prices, balances=None, fee_rate=0.001, lot=1e-6, min_cost=5.0):
        self.prices = prices              # {symbol: price}
        self.balances = dict(balances or {"USDT": 10_000.0})
        self.fee_rate, self.lot, self._min_cost = fee_rate, lot, min_cost
        self.orders = {}                  # client_id -> Fill
        self.sent = []                    # client ids in send order
        self.fail_next = None             # exception to raise on the next order

    def min_cost(self, symbol):
        return self._min_cost

    def round_amount(self, symbol, amount):
        return math.floor(amount / self.lot) * self.lot

    def free_balance(self, asset):
        return self.balances.get(asset, 0.0)

    def find_order(self, symbol, client_id):
        return self.orders.get(client_id)

    def _order(self, symbol, side, amount, client_id):
        if self.fail_next:
            exc, self.fail_next = self.fail_next, None
            raise exc
        price = self.prices[symbol]
        base, quote = symbol.split("/")
        cost = amount * price
        fee_quote = cost * self.fee_rate
        fee_base = amount * self.fee_rate if side == "buy" else 0.0
        if side == "buy":
            self.balances[quote] = self.balances.get(quote, 0) - cost
            self.balances[base] = self.balances.get(base, 0) + amount - fee_base
        else:
            self.balances[base] = self.balances.get(base, 0) - amount
            self.balances[quote] = self.balances.get(quote, 0) + cost - fee_quote
        fill = Fill(client_id, f"o{len(self.sent) + 1}", symbol, side, "closed", amount, cost, price,
                    fee_quote, fee_base)
        self.orders[client_id] = fill
        self.sent.append(client_id)
        return fill

    def market_buy(self, symbol, quote_amount, client_id):
        amount = self.round_amount(symbol, quote_amount / self.prices[symbol])
        return self._order(symbol, "buy", amount, client_id)

    def market_sell(self, symbol, amount, client_id):
        return self._order(symbol, "sell", amount, client_id)
