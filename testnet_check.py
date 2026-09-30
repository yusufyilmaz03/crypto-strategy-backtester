# testnet_check.py
"""End-to-end check of the order flow on Binance Spot Testnet.

Without --yes it only connects and shows balances. With --yes it buys about
10 USDT of BTC with a market order, sells it again, looks both orders up by their
client order id and reports fills, fees and slippage. Refuses to run in live mode.
"""
import argparse
import time

import pandas as pd

import config
from broker import CcxtBroker


def main(place_orders, symbol="BTC/USDT", quote=10.0):
    if config.TRADING_MODE != "testnet":
        raise SystemExit("testnet_check.py only runs with TRADING_MODE = 'testnet'.")
    b = CcxtBroker(mode="testnet")
    base, q = symbol.split("/")
    print(f"✅ Connected to {b.ex.urls['api']['public']}")
    print(f"   {q} free: {b.free_balance(q):.2f} | {base} free: {b.free_balance(base):.8f}")
    print(f"   {symbol} min order: {b.min_cost(symbol)} {q}")
    if not place_orders:
        print("Run with --yes to place a small test buy and sell.")
        return

    ref = b.ex.fetch_ticker(symbol)["last"]
    stamp = pd.Timestamp.now(tz="UTC")
    buy_id = f"kb-check-{int(stamp.timestamp())}-b"
    buy = b.market_buy(symbol, quote, buy_id)
    print(f"🟢 BUY  {buy.filled:.8f} {base} @ {buy.average:.2f} (ref {ref:.2f}, "
          f"slippage {(buy.average / ref - 1) * 1e4:+.1f} bps) fee {buy.fee_quote:.4f} {q}, "
          f"received {buy.net_base:.8f}")

    time.sleep(1)
    amount = b.round_amount(symbol, min(buy.net_base, b.free_balance(base)))
    sell_id = f"kb-check-{int(stamp.timestamp())}-s"
    sell = b.market_sell(symbol, amount, sell_id)
    print(f"🔴 SELL {sell.filled:.8f} {base} @ {sell.average:.2f} fee {sell.fee_quote:.4f} {q}")

    for cid in (buy_id, sell_id):
        found = b.find_order(symbol, cid)
        print(f"🔎 lookup {cid}: {'found, status ' + found.status if found else 'NOT FOUND'}")
    net = sell.cost - sell.fee_quote - buy.cost
    print(f"Round trip result: {net:+.4f} {q} (fees and spread)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true", help="place the test orders")
    main(ap.parse_args().yes)
