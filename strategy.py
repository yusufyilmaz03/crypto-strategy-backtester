# strategy.py
"""Signal generators.

Every strategy takes an OHLCV DataFrame with indicators (see indicators.add_indicators)
and returns a Series aligned with it holding "BUY", "SELL" or "-" for each candle.
The value at row i only depends on rows 0..i, so it can be acted on once candle i
has closed (tests/test_strategy.py checks this for every strategy).
"""
import numpy as np
import pandas as pd

from indicators import atr as _atr

# ==========================
# Helper calculations
# ==========================

def _ema(series: pd.Series, length: int):
    return series.ewm(span=length, adjust=False).mean()

def _bbands(close: pd.Series, length: int = 20, mult: float = 2.0):
    ma = close.rolling(length).mean()
    std = close.rolling(length).std(ddof=0)
    upper = ma + mult * std
    lower = ma - mult * std
    return ma, upper, lower

def _donchian(df: pd.DataFrame, length: int = 20):
    upper = df["high"].rolling(length).max()
    lower = df["low"].rolling(length).min()
    return upper, lower

def _ensure_cols(df: pd.DataFrame, need_cols):
    """Compute any of the required indicator columns that are missing (on a copy)."""
    out = df.copy()

    if "EMA_9" in need_cols and "EMA_9" not in out.columns:
        out["EMA_9"] = _ema(out["close"], 9)
    if "EMA_21" in need_cols and "EMA_21" not in out.columns:
        out["EMA_21"] = _ema(out["close"], 21)
    if "EMA_50" in need_cols and "EMA_50" not in out.columns:
        out["EMA_50"] = _ema(out["close"], 50)
    if "ATR" in need_cols and "ATR" not in out.columns:
        out["ATR"] = _atr(out, 14)
    if any(c in need_cols for c in ["BB_MA_20", "BB_UPPER_20", "BB_LOWER_20"]):
        ma, up, low = _bbands(out["close"], 20, 2.0)
        out["BB_MA_20"] = ma
        out["BB_UPPER_20"] = up
        out["BB_LOWER_20"] = low
    if any(c in need_cols for c in ["DONCHIAN_UP_20", "DONCHIAN_LO_20"]):
        up, lo = _donchian(out, 20)
        out["DONCHIAN_UP_20"] = up
        out["DONCHIAN_LO_20"] = lo

    return out

def _to_signals(df, buy, sell, warmup):
    """Combine boolean BUY/SELL conditions into a signal Series.

    BUY takes precedence over SELL; the first `warmup` rows are always "-".
    """
    sig = pd.Series(np.select([buy, sell], ["BUY", "SELL"], default="-"), index=df.index)
    sig.iloc[:warmup] = "-"
    return sig


# ==========================
# v1: Simple RSI + EMA crossover
# ==========================
def generate_signals_v1(df):
    df = _ensure_cols(df, ["EMA_9", "EMA_21"])
    rsi, ema9, ema21 = df["RSI"], df["EMA_9"], df["EMA_21"]
    buy = (rsi < 40) & (ema9 > ema21)
    sell = (rsi > 60) & (ema9 < ema21)
    return _to_signals(df, buy, sell, warmup=20)


# ==========================================
# v2: RSI + EMA, confirmed on the last 3 candles
# ==========================================
def generate_signals_v2(df):
    df = _ensure_cols(df, ["EMA_9", "EMA_21"])
    rsi, ema9, ema21 = df["RSI"], df["EMA_9"], df["EMA_21"]
    buy_now = ((rsi < 45) & (ema9 > ema21)).astype(float)
    sell_now = ((rsi > 55) & (ema9 < ema21)).astype(float)
    buy = buy_now.rolling(3).min() == 1
    sell = sell_now.rolling(3).min() == 1
    return _to_signals(df, buy, sell, warmup=24)


# =====================================
# v3: RSI + EMA with ATR for stop-loss sizing
# =====================================
def generate_signals_v3(df):
    df = _ensure_cols(df, ["EMA_9", "EMA_21"])
    rsi, ema9, ema21 = df["RSI"], df["EMA_9"], df["EMA_21"]
    buy = (rsi < 45) & (ema9 > ema21)
    sell = (rsi > 55) & (ema9 < ema21)
    return _to_signals(df, buy, sell, warmup=14)


# ====================================================
# v4: Bollinger mean reversion (BBANDS + RSI filter)
# - Overextended move -> expect reversion to the mean
# - BUY: close < lower band and RSI < 35
# - SELL: close > upper band and RSI > 65
# ====================================================
def generate_signals_v4_bbands_meanrev(df):
    df = _ensure_cols(df, ["BB_MA_20", "BB_UPPER_20", "BB_LOWER_20"])
    rsi, close = df["RSI"], df["close"]
    buy = (close < df["BB_LOWER_20"]) & (rsi < 35)
    sell = (close > df["BB_UPPER_20"]) & (rsi > 65)
    return _to_signals(df, buy, sell, warmup=24)


# ====================================================
# v5: EMA cross + trend filter (trade with the trend only)
# - BUY: EMA9>EMA21 and close>EMA21 and RSI>45
# - SELL: EMA9<EMA21 and close<EMA21 and RSI<55
# (Filtering out counter-trend crosses reduces whipsaws)
# ====================================================
def generate_signals_v5_ema_cross_filter(df):
    df = _ensure_cols(df, ["EMA_9", "EMA_21"])
    rsi, ema9, ema21, close = df["RSI"], df["EMA_9"], df["EMA_21"], df["close"]
    buy = (ema9 > ema21) & (close > ema21) & (rsi > 45)
    sell = (ema9 < ema21) & (close < ema21) & (rsi < 55)
    return _to_signals(df, buy, sell, warmup=24)


# ====================================================
# v6: Donchian breakout (20) + ATR threshold
# - BUY: close > 20-bar high and (close - high_prev) > 0.2*ATR
# - SELL: close < 20-bar low  and (low_prev - close) > 0.2*ATR
# (ATR threshold filters out false breakouts)
# ====================================================
def generate_signals_v6_donchian_breakout(df, atr_thresh=0.2):
    df = _ensure_cols(df, ["DONCHIAN_UP_20", "DONCHIAN_LO_20", "ATR"])
    close, atr = df["close"], df["ATR"]
    # Channel bounds of the previous bar (a breakout is measured against the prior bar's channel)
    up_prev = df["DONCHIAN_UP_20"].shift(1)
    lo_prev = df["DONCHIAN_LO_20"].shift(1)
    # Is the move beyond the channel significant?
    buy = (close > up_prev) & ((close - up_prev) > atr_thresh * atr)
    sell = (close < lo_prev) & ((lo_prev - close) > atr_thresh * atr)
    return _to_signals(df, buy, sell, warmup=20)


# ====================================================
# v7: Trend + RSI pullback/cross
# - Trend filter: EMA21 > EMA50 -> LONG only; EMA21 < EMA50 -> SHORT only
# - LONG: RSI dipped below 40, then crosses back above 45 (pullback is over)
# - SHORT: RSI rose above 60, then crosses back below 55
# ====================================================
def generate_signals_v7_trend_pullback_rsi(df):
    df = _ensure_cols(df, ["EMA_21", "EMA_50"])
    rsi = df["RSI"]
    rsi_prev = rsi.shift(1)
    uptrend = df["EMA_21"] > df["EMA_50"]
    downtrend = df["EMA_21"] < df["EMA_50"]
    # RSI was below 40 / above 60 within the 5 candles before the current one
    was_oversold = rsi.rolling(5).min().shift(1) < 40
    was_overbought = rsi.rolling(5).max().shift(1) > 60
    buy = uptrend & was_oversold & (rsi_prev < 45) & (rsi >= 45)
    sell = downtrend & was_overbought & (rsi_prev > 55) & (rsi <= 55)
    return _to_signals(df, buy, sell, warmup=54)


# ==========================
# Strategy registry
# ==========================
STRATEGY_DISPATCH = {
    "v1": generate_signals_v1,
    "v2": generate_signals_v2,
    "v3": generate_signals_v3,
    "v4": generate_signals_v4_bbands_meanrev,
    "v5": generate_signals_v5_ema_cross_filter,
    "v6": generate_signals_v6_donchian_breakout,
    "v7": generate_signals_v7_trend_pullback_rsi,
}


def get_strategy(key):
    """Return the signal function registered under `key`."""
    if key not in STRATEGY_DISPATCH:
        raise ValueError(f"Unknown strategy: {key}")
    return STRATEGY_DISPATCH[key]
