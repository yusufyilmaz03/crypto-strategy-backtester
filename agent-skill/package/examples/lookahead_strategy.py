# lookahead_strategy.py
"""A deliberately broken strategy: it buys when the NEXT candle closes higher.

It looks spectacular in a naive backtest and is impossible to trade. The `check`
command must reject it (see examples/04-custom-strategy.md). Do not use it for
anything else.
"""
import numpy as np
import pandas as pd


def generate_signals(df):
    close = df["close"]
    next_close = close.shift(-1)          # lookahead: reads the following candle
    buy = next_close > close
    sell = next_close < close
    return pd.Series(np.select([buy, sell], ["BUY", "SELL"], default="-"), index=df.index)
