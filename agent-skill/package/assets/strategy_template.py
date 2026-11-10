# strategy_template.py
"""Template for a strategy that none of the built-in v1-v10 can express.

Copy this file, change the rule, then run:

    python scripts/backtest.py check --strategy-file my_strategy.py --symbols BTC/USDT --timeframe 1d
    python scripts/backtest.py walkforward --strategy-file my_strategy.py --symbols BTC/USDT ETH/USDT --timeframe 1d --days 1460

Contract
- generate_signals(df, **params) returns a pandas Series aligned with df holding
  "BUY", "SELL" or "-" for every candle.
- df has one row per CLOSED candle: timestamp, open, high, low, close, volume,
  plus RSI (14, Wilder), EMA_9, EMA_21 and ATR (14, Wilder).
- The signal at row i may only use rows 0..i. The engine acts on it at the open
  of candle i+1. Long-only: BUY opens a position, SELL closes it.

Rules that keep it free of lookahead
- Only backward-looking operations: rolling(), ewm(), expanding(), shift(n) with n >= 0.
- Never shift(-n), center=True, bfill, or a statistic of the whole series
  (df["close"].max(), .mean(), .quantile() without rolling/expanding).
- Leave the first `warmup` rows as "-" until every indicator has a value.

Example rule: MACD line crosses above its signal line while the close is above
SMA(trend); exit when MACD crosses back below the signal line.
"""
import numpy as np
import pandas as pd

# Optional. Used by the walkforward command; keep it small, every extra
# combination is another chance to fit noise. A tuple of names varies together.
PARAM_GRID = {
    ("fast", "slow"): [(12, 26), (8, 21)],
    "trend": [100, 200],
}


def generate_signals(df, fast=12, slow=26, signal=9, trend=200):
    close = df["close"]
    macd = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    macd_signal = macd.ewm(span=signal, adjust=False).mean()
    sma_trend = close.rolling(trend).mean()

    above = macd > macd_signal
    crossed_up = above & ~above.shift(1, fill_value=False)
    crossed_down = ~above & above.shift(1, fill_value=False)

    buy = crossed_up & (close > sma_trend)
    sell = crossed_down

    out = pd.Series(np.select([buy, sell], ["BUY", "SELL"], default="-"), index=df.index)
    out.iloc[:max(trend, slow + signal)] = "-"      # warm-up
    return out
