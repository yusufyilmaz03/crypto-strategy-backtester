# kripto_bot/core/sizing.py
"""Portfolio weights shared by research and the paper bot (docs/v9-sizing-test.md)."""
import math

import pandas as pd

LOOKBACK = 60       # days of returns behind the volatility estimate (pre-registered)


def volatility(close, lookback=LOOKBACK):
    """Standard deviation of the last `lookback` close-to-close returns of a close Series
    that ends at the decision time (nothing later)."""
    r = close.iloc[-(lookback + 1):].pct_change().dropna()
    return float(r.std(ddof=1)) if len(r) >= 2 else float("nan")


def inverse_vol_weights(closes, lookback=LOOKBACK):
    """{name: weight} proportional to 1/σ, summing to 1. A name without a usable σ (too
    little history, no movement) gets the median σ of the others; if none is usable the
    weights are equal."""
    sigma = {k: volatility(c, lookback) for k, c in closes.items()}
    good = [s for s in sigma.values() if s > 0 and not math.isnan(s)]
    if not good:
        return {k: 1 / len(closes) for k in closes}
    fill = float(pd.Series(good).median())
    inv = {k: 1 / (s if s > 0 and not math.isnan(s) else fill) for k, s in sigma.items()}
    total = sum(inv.values())
    return {k: v / total for k, v in inv.items()}
