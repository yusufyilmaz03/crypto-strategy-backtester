# indicators.py
import numpy as np
import pandas as pd


def rma(series, length):
    """Wilder's moving average (TradingView ta.rma): SMA seed, then recursive smoothing."""
    values = series.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    valid = np.flatnonzero(~np.isnan(values))
    if len(valid) < length:
        return pd.Series(out, index=series.index)
    start = valid[0] + length - 1
    out[start] = values[valid[0]:start + 1].mean()
    for i in range(start + 1, len(values)):
        out[i] = out[i - 1] + (values[i] - out[i - 1]) / length
    return pd.Series(out, index=series.index)


def rsi(close, length=14):
    """RSI with Wilder smoothing, matching TradingView/Binance charts."""
    delta = close.diff()
    avg_gain = rma(delta.clip(lower=0), length)
    avg_loss = rma(-delta.clip(upper=0), length)
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    return out.where(avg_loss != 0, 100.0).where(avg_gain.notna())


def atr(df, length=14):
    """Average True Range with Wilder smoothing (TradingView ta.atr)."""
    prev_close = df["close"].shift()
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return rma(tr, length)


def add_indicators(df):
    df["RSI"] = rsi(df["close"], 14)

    # EMAs
    df["EMA_9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["EMA_21"] = df["close"].ewm(span=21, adjust=False).mean()

    df["ATR"] = atr(df, 14)

    return df
