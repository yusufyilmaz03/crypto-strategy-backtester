# kripto_bot/llm/prompt.py
"""The v11 prompt: what the model sees (docs/v11-ai-analyst.md, "What the model sees").

Everything here is a pure function of data passed in, so a prompt can be rebuilt and audited.
A call covers one or more symbols; the answer is always {"plans": [...]}, one plan per symbol,
so a single-symbol call and a group of five differ only in how many symbol blocks they hold.
"""
import re
from dataclasses import dataclass, field

import pandas as pd

from kripto_bot.core import indicators
from kripto_bot.llm import plan as pl

RETURN_DAYS = (1, 7, 30, 90)
EXTREMES_DAYS = 20
VOLUME_DAYS = 30
CONTEXT_DAYS = 7
NEWS_HOURS = 72
MAX_COIN_NEWS = 10
MAX_GENERAL_NEWS = 15

# Names news uses for a coin besides its ticker. A ticker alone is matched as a whole
# upper-case word, and only when it has 3 or more letters (UNI or S would match too much).
COIN_NAMES = {
    "BTC": ["Bitcoin"], "ETH": ["Ethereum", "Ether"], "SOL": ["Solana"], "XRP": ["Ripple"],
    "BNB": ["BNB Chain"], "DOGE": ["Dogecoin"], "ADA": ["Cardano"], "TRX": ["Tron"],
    "AVAX": ["Avalanche"], "LINK": ["Chainlink"], "DOT": ["Polkadot"], "LTC": ["Litecoin"],
    "NEAR": ["NEAR Protocol"], "SUI": ["Sui"], "ZEC": ["Zcash"], "PEPE": ["Pepe"],
    "UNI": ["Uniswap"], "ENA": ["Ethena"], "WLD": ["Worldcoin", "World Network"],
    "PUMP": ["Pump.fun"], "STRK": ["Starknet"], "TAO": ["Bittensor"], "AAVE": ["Aave"],
    "TON": ["Toncoin"], "SHIB": ["Shiba Inu"], "APT": ["Aptos"], "ARB": ["Arbitrum"],
    "OP": ["Optimism"], "ATOM": ["Cosmos"], "HBAR": ["Hedera"], "XLM": ["Stellar"],
    "ONDO": ["Ondo"], "INJ": ["Injective"], "QNT": ["Quant"], "FIL": ["Filecoin"],
}


def market_summary(df):
    """Numbers from closed daily candles (oldest first, columns open/high/low/close/volume)."""
    close = df["close"].reset_index(drop=True)
    last = float(close.iloc[-1])
    out = {"last": last}
    for n in RETURN_DAYS:
        out[f"ret_{n}d"] = last / float(close.iloc[-1 - n]) - 1 if len(close) > n else None
    for n in (50, 200):
        out[f"sma{n}_dist"] = last / float(close.iloc[-n:].mean()) - 1 if len(close) >= n else None
    atr = float(indicators.atr(df.reset_index(drop=True)).iloc[-1])
    out["atr"], out["atr_pct"] = atr, atr / last
    out["highs"] = [float(x) for x in df["high"].iloc[-EXTREMES_DAYS:]]
    out["lows"] = [float(x) for x in df["low"].iloc[-EXTREMES_DAYS:]]
    vol = df["volume"].reset_index(drop=True)
    if len(vol) >= 2 * VOLUME_DAYS:
        out["volume_change"] = float(vol.iloc[-VOLUME_DAYS:].sum() / vol.iloc[-2 * VOLUME_DAYS:-VOLUME_DAYS].sum() - 1)
    else:
        out["volume_change"] = None
    return out


def _pct(x):
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def _price(x, tick):
    decimals = max(0, -int(f"{tick:e}".split("e")[1]))
    return f"{x:.{decimals}f}"


def summary_lines(s, tick):
    rets = ", ".join(f"{n}d {_pct(s[f'ret_{n}d'])}" for n in RETURN_DAYS)
    return [
        f"- Last price: {_price(s['last'], tick)} (price step {tick:g})",
        f"- Returns: {rets}",
        f"- Distance from SMA50: {_pct(s['sma50_dist'])}; from SMA200: {_pct(s['sma200_dist'])}",
        f"- Daily ATR(14): {_price(s['atr'], tick)} ({s['atr_pct'] * 100:.2f}% of price)",
        f"- Last {EXTREMES_DAYS} daily highs (oldest first): " + ", ".join(_price(x, tick) for x in s["highs"]),
        f"- Last {EXTREMES_DAYS} daily lows (oldest first): " + ", ".join(_price(x, tick) for x in s["lows"]),
        f"- {VOLUME_DAYS}-day volume vs the {VOLUME_DAYS} days before: {_pct(s['volume_change'])}",
    ]


def mentions(text, base):
    """Does a headline or summary mention the coin?"""
    names = COIN_NAMES.get(base, [])
    if any(re.search(rf"\b{re.escape(n)}\b", text, re.IGNORECASE) for n in names):
        return True
    return len(base) >= 3 and re.search(rf"(?<![A-Za-z]){re.escape(base)}(?![A-Za-z])", text) is not None


def split_news(items, bases):
    """{base: its headlines} and the general headlines that mention none of the bases."""
    per = {b: [] for b in bases}
    general = []
    for item in items:
        text = f"{item['title']} {item.get('summary', '')}"
        hit = [b for b in bases if mentions(text, b)]
        for b in hit:
            per[b].append(item)
        if not hit:
            general.append(item)
    return {b: v[:MAX_COIN_NEWS] for b, v in per.items()}, general[:MAX_GENERAL_NEWS]


def news_lines(items):
    if not items:
        return ["- (none)"]
    return [f"- [{i['id']}] {i['published_at'][:16].replace('T', ' ')} UTC, {i['source']}: \"{i['title']}\""
            + (f" — {i['summary']}" if i.get("summary") else "") for i in items]


@dataclass
class SymbolInput:
    symbol: str
    tick: float
    summary: dict
    state: list = field(default_factory=list)     # lines about the position, orders and current plan
    history: list = field(default_factory=list)   # the last plans with their outcome, one line each
    memo: str = ""


@dataclass
class Context:
    btc_summary: dict
    fear_greed: list = field(default_factory=list)      # [(date, value, label)], oldest first
    btc_dominance: list = field(default_factory=list)   # [(date, value)]
    eth_dominance: list = field(default_factory=list)


RULES = f"""## Rules the plan must satisfy (a plan that breaks any rule is rejected and the previous plan stays)
- action: hold (keep orders as they are), enter (place an entry order), adjust (change stop, take-profit or review levels of the position or the pending entry), cancel (cancel the pending entry), exit (sell the position at market).
- Spot, long only, one position per symbol, no adding to a position, no leverage. A new enter replaces a pending entry.
- Stop below the entry (or below the last price for a position), {pl.STOP_MIN_ATR} to {pl.STOP_MAX_ATR:g} daily ATRs away. A stop can only be raised; an unchanged stop is always accepted.
- Limit buy at or below the last price and at most {pl.LIMIT_MAX_ATR:g} daily ATRs below it. A market entry fills at the next minute's open.
- Take-profit, if given, at least {pl.TAKE_PROFIT_MIN_ATR:g} daily ATR above the entry (and above the last price for a position).
- Entry orders expire after at most {pl.MAX_EXPIRY_CANDLES} daily closes (expires_candles 1 to {pl.MAX_EXPIRY_CANDLES}).
- review_if: up to {pl.MAX_REVIEW_LEVELS} price levels, each at least {pl.REVIEW_MIN_ATR} daily ATRs from the last price; reaching one calls you again. The levels and memo of every accepted plan replace the previous ones.
- p_tp: your probability ({pl.P_TP_MIN} to {pl.P_TP_MAX}) that, if the entry fills, the take-profit is reached before the stop within 30 days of the fill. Required for an enter plan with a take-profit and when you change the stop or take-profit of a pending entry; otherwise null.
- grade: weak / normal / strong / very strong for an enter plan; otherwise null.
- Prices use the symbol's price step. Fields that do not apply are null or empty lists.
- reasons: 1 to 4 short sentences. news_ids: ids of the headlines you relied on.
- memo: a note to yourself of at most {pl.MEMO_MAX_CHARS} characters, shown on your next call for this symbol and then replaced; may be empty.
- Fills, stops and take-profits are executed by the bot every minute without asking you. You are called after each daily close and when a review level is reached."""


def build(call_time, symbols, ctx, news=None, kind="scheduled"):
    """The prompt for one call. `news` is a list of headline dicts, or None for the no-news control."""
    call_time = pd.Timestamp(call_time)
    lines = [
        "You are the analyst of a paper-trading bot on Binance spot. You write one trading plan per symbol below. "
        "The bot validates each plan against fixed limits and executes it. You have no tools: do not run commands, "
        "read files or browse. Use only the data below. Answer with the JSON object only: "
        '{"plans": [...]}, one plan per symbol, in the order given.',
        "",
        RULES,
        "",
        "## Time",
        f"Call time: {call_time.strftime('%Y-%m-%d %H:%M:%S')} UTC ({kind} review). All data below is from closed candles.",
        "",
        "## Market context: BTC/USDT",
        *summary_lines(ctx.btc_summary, 0.01),
        "",
        "## Sentiment and structure (last 7 days, oldest first)",
        "- Fear & Greed: " + (", ".join(f"{v:g} ({lab})" for _, v, lab in ctx.fear_greed) or "n/a"),
        "- BTC dominance: " + (", ".join(f"{v:.1f}%" for _, v in ctx.btc_dominance) or "n/a"),
        "- ETH dominance: " + (", ".join(f"{v:.1f}%" for _, v in ctx.eth_dominance) or "n/a"),
    ]
    bases = [s.symbol.split("/")[0] for s in symbols]
    per, general = split_news(news, bases) if news is not None else ({}, [])
    if news is not None:
        lines += ["", f"## General market headlines (last {NEWS_HOURS} h, published and fetched before the call)",
                  *news_lines(general)]
    for s, base in zip(symbols, bases):
        lines += ["", f"## Symbol: {s.symbol}", *summary_lines(s.summary, s.tick),
                  "", "Your state for this symbol:", *(s.state or ["- Position: flat. Open orders: none."]),
                  "Your last plans and what happened:", *(s.history or ["- (none yet)"]),
                  f"Your memo from the last call: {s.memo!r}" if s.memo else "Your memo from the last call: (none)"]
        if news is not None:
            lines += [f"Headlines mentioning {base}:", *news_lines(per[base])]
    return "\n".join(lines) + "\n"
