# kripto_bot/core/universe.py
"""A point-in-time universe: the N most traded USDT spot pairs on Binance right now.

v11 picks its 20 symbols this way once, on its start date, from every listed pair rather
than config.SYMBOLS (docs/v11-ai-analyst.md, open question 3). The rules are the survivorship
test's (analysis/survivorship.py): its exclusions (stablecoins, fiat and gold tokens,
leveraged tokens, tokenized stocks), at least 60 closed daily candles with one yesterday, and
the quote volume of the last 30 closed days. One more guard, because a new stablecoin would
not be in STABLE_BASES: a pair whose closes over those 30 days vary by less than 2% is left out.

Every pair left out is listed with its reason, so a person can check the choice before it is
fixed.
"""
import argparse
import glob
import json
import os
import statistics

import pandas as pd

from kripto_bot import paths
from kripto_bot.analysis import survivorship
from kripto_bot.core import data

QUOTE = survivorship.QUOTE
MIN_HISTORY = survivorship.MIN_HISTORY
VOLUME_DAYS = survivorship.VOLUME_DAYS
MIN_PRICE_CV = 0.02
DAY_MS = 86_400_000


def candidates(markets):
    """(symbol, exchange id, base) of every active USDT spot pair."""
    return sorted((m["symbol"], m["id"], m["base"]) for m in markets.values()
                  if m.get("spot") and m.get("quote") == QUOTE and m.get("active"))


def klines(exchange, market_id, **params):
    """Daily candles as (open time ms, close, quote volume), from Binance's raw klines."""
    raw = exchange.publicGetKlines({"symbol": market_id, "interval": "1d", **params})
    return [(int(r[0]), float(r[4]), float(r[7])) for r in raw]


def assess(rows, today_ms):
    """(reason to leave the pair out or None, 30-day quote volume) from its daily candles."""
    closed = [r for r in rows if r[0] < today_ms]
    if len(closed) < MIN_HISTORY:
        return f"fewer than {MIN_HISTORY} closed daily candles", 0.0
    if closed[-1][0] != today_ms - DAY_MS:
        return "no candle yesterday", 0.0
    recent = closed[-VOLUME_DAYS:]
    closes = [c for _, c, _ in recent]
    cv = statistics.pstdev(closes) / statistics.fmean(closes)
    if cv < MIN_PRICE_CV:
        return f"price barely moves (variation {cv:.2%})", 0.0
    return None, sum(qv for _, _, qv in recent)


def select(n, exchange=None, now=None, log=print):
    """The n most traded eligible pairs, with the full ranking and every pair left out."""
    exchange = exchange or data.get_exchange()
    now = now or pd.Timestamp.now(tz="UTC")
    today_ms = int(now.normalize().timestamp() * 1000)
    cands = candidates(exchange.load_markets())
    bases = {b for _, _, b in cands}
    log(f"{len(cands)} active {QUOTE} spot pairs")
    ranked, skipped = [], []
    for symbol, market_id, base in cands:
        if survivorship.excluded(base, bases):
            skipped.append({"symbol": symbol, "reason": "stablecoin, fiat, gold or leveraged token"})
            continue
        try:
            if base.endswith("B"):
                first = klines(exchange, market_id, startTime=0, limit=1)
                if first and pd.Timestamp(first[0][0], unit="ms") >= survivorship.STOCK_TOKENS_FROM:
                    skipped.append({"symbol": symbol, "reason": "tokenized stock or ETF"})
                    continue
            reason, volume = assess(klines(exchange, market_id, limit=MIN_HISTORY + 1), today_ms)
        except Exception as e:      # one pair's failure must not hide the others; it is listed
            skipped.append({"symbol": symbol, "reason": f"fetch failed: {e}"})
            continue
        if reason:
            skipped.append({"symbol": symbol, "reason": reason})
        else:
            ranked.append({"symbol": symbol, "quote_volume_30d": round(volume, 2)})
    ranked.sort(key=lambda r: r["quote_volume_30d"], reverse=True)
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return {"as_of": now.isoformat(), "n": n, "symbols": [r["symbol"] for r in ranked[:n]],
            "ranking": ranked, "skipped": skipped}


def saved():
    """Universes saved with --save, newest first: [{file, date, symbols}] (the panel offers them)."""
    out = []
    for path in sorted(glob.glob(paths.results("universe_*.json")), reverse=True):
        try:
            with open(path) as f:
                u = json.load(f)
            out.append({"file": os.path.basename(path), "date": u["as_of"][:10], "symbols": u["symbols"]})
        except (OSError, ValueError, KeyError):
            continue
    return out


def main():
    ap = argparse.ArgumentParser(description=f"The N most traded {QUOTE} spot pairs on Binance "
                                             f"over the last {VOLUME_DAYS} closed days.")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--save", action="store_true", help="write results/universe_<date>.json")
    a = ap.parse_args()
    out = select(a.n)
    for r in out["ranking"][:a.n + 5]:
        mark = "" if r["rank"] <= a.n else "   (next)"
        print(f"{r['rank']:>3}  {r['symbol']:<14} {r['quote_volume_30d'] / 1e6:>12,.0f} M{mark}")
    failed = [s for s in out["skipped"] if s["reason"].startswith("fetch failed")]
    print(f"{len(out['ranking'])} eligible, {len(out['skipped'])} left out"
          + (f", {len(failed)} of them because a fetch failed: run again" if failed else ""))
    if a.save:
        path = paths.results(f"universe_{out['as_of'][:10]}.json")
        with open(path, "w") as f:
            json.dump(out, f, indent=1)
        print(f"saved {path}")


if __name__ == "__main__":
    main()
