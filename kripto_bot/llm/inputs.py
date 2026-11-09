# kripto_bot/llm/inputs.py
"""Gathers what a v11 call needs at its call time: daily candles, price steps, the market
context and the headlines. Only data that existed before the call is used: closed candles,
context values and headlines fetched before it.
"""
from dataclasses import dataclass

import pandas as pd

from kripto_bot.core import data
from kripto_bot.core import market_context as mc
from kripto_bot.core import news as nw
from kripto_bot.llm import plan as pl
from kripto_bot.llm import prompt as pr

DAILY_CANDLES = 260          # enough for SMA200 and a 90-day return


def _ms(t):
    return int(pd.Timestamp(t).timestamp() * 1000)


def daily(symbol, call_time, exchange=None):
    """Closed daily candles before the call, oldest first."""
    return data.fetch_recent(symbol, "1d", limit=DAILY_CANDLES, exchange=exchange, now=_ms(call_time))


def ticks(symbols, exchange=None):
    ex = exchange or data.get_exchange()
    markets = ex.load_markets()
    return {s: float(markets[s]["precision"]["price"]) for s in symbols}


def market_context(call_time, path=None):
    """(Fear & Greed [(date, value, label)], BTC dominance, ETH dominance), the last 7 recorded
    days, each value fetched before the call."""
    ctx = mc.MarketContext(path)
    try:
        df = ctx.frame()
    finally:
        ctx.close()
    if df.empty:
        return [], [], []
    df = df[pd.to_datetime(df["fetched_at"], utc=True) < pd.Timestamp(call_time)]
    fg = df[df["source"] == "fear_greed"].sort_values("date").tail(pr.CONTEXT_DAYS)
    glob = df[df["source"] == "coingecko_global"]

    def dom(field):
        d = glob[glob["field"] == field].sort_values("date").tail(pr.CONTEXT_DAYS)
        return list(zip(d["date"], d["value"]))

    return list(zip(fg["date"], fg["value"], fg["label"])), dom("btc_dominance"), dom("eth_dominance")


def headlines(call_time, path=None):
    news = nw.News(path)
    try:
        return news.items(pd.Timestamp(call_time), hours=pr.NEWS_HOURS).to_dict("records")
    finally:
        news.close()


@dataclass
class Snapshot:
    """Everything a call at `time` may see, gathered once and shared by every call made from it."""
    time: pd.Timestamp
    context: pr.Context
    symbols: dict            # symbol -> pr.SymbolInput (flat state; the analyst adds its own)
    news: list


def snapshot(symbols, call_time, exchange=None, db_path=None, log=print):
    call_time = pd.Timestamp(call_time)
    steps = ticks(symbols + ["BTC/USDT"], exchange)
    btc = pr.market_summary(daily("BTC/USDT", call_time, exchange))
    fg, btc_dom, eth_dom = market_context(call_time, db_path)
    inputs = {}
    for s in symbols:
        try:
            inputs[s] = pr.SymbolInput(s, steps[s], pr.market_summary(daily(s, call_time, exchange)))
        except Exception as e:      # a symbol without data is left out of this call and logged
            log(f"[V11 INPUT ERROR] {s}: {type(e).__name__}: {e}")
    return Snapshot(call_time, pr.Context(btc, fg, btc_dom, eth_dom), inputs, headlines(call_time, db_path))


def validator_context(inp, last_price=None, position=None, pending=None):
    s = inp.summary
    return pl.Context(inp.symbol, s["last"] if last_price is None else last_price, s["atr"], inp.tick,
                      position, pending)
