# strategy.py
import pandas as pd
import numpy as np

# ==========================
# Helper calculations
# ==========================

def _ema(series: pd.Series, length: int):
    return series.ewm(span=length, adjust=False).mean()

def _atr(df: pd.DataFrame, length: int = 14):
    # True Range
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(length).mean()

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


# ==========================
# v1: Simple RSI + EMA crossover
# ==========================
def generate_signals_v1(df):
    if df is None or len(df) < 21:
        return "-", None, None

    df = _ensure_cols(df, ["EMA_9", "EMA_21", "ATR"])
    rsi = df["RSI"].iloc[-1]
    ema9 = df["EMA_9"].iloc[-1]
    ema21 = df["EMA_21"].iloc[-1]
    atr = df["ATR"].iloc[-1]

    if rsi < 40 and ema9 > ema21:
        return "BUY", rsi, atr
    elif rsi > 60 and ema9 < ema21:
        return "SELL", rsi, atr
    else:
        return "-", rsi, atr


# ==========================================
# v2: RSI + EMA, confirmed on the last 3 candles
# ==========================================
def generate_signals_v2(df):
    if df is None or len(df) < 25:
        return "-", None, None

    df = _ensure_cols(df, ["EMA_9", "EMA_21", "ATR"])
    recent = df.iloc[-3:]  # last 3 candles

    buy_cond = (recent["RSI"] < 45) & (recent["EMA_9"] > recent["EMA_21"])
    sell_cond = (recent["RSI"] > 55) & (recent["EMA_9"] < recent["EMA_21"])

    rsi = df["RSI"].iloc[-1]
    atr = df["ATR"].iloc[-1]

    if buy_cond.all():
        return "BUY", rsi, atr
    elif sell_cond.all():
        return "SELL", rsi, atr
    else:
        return "-", rsi, atr


# =====================================
# v3: RSI + EMA with ATR for stop-loss sizing
# =====================================
def generate_signals_v3(df, atr_multiplier=1.5):
    if df is None or len(df) < 15:
        return "-", None, None

    df = _ensure_cols(df, ["EMA_9", "EMA_21", "ATR"])
    rsi = df["RSI"].iloc[-1]
    ema9 = df["EMA_9"].iloc[-1]
    ema21 = df["EMA_21"].iloc[-1]
    atr = df["ATR"].iloc[-1]

    if rsi < 45 and ema9 > ema21:
        return "BUY", rsi, atr
    elif rsi > 55 and ema9 < ema21:
        return "SELL", rsi, atr
    else:
        return "-", rsi, atr


# ====================================================
# v4: Bollinger mean reversion (BBANDS + RSI filter)
# - Overextended move -> expect reversion to the mean
# - BUY: close < lower band and RSI < 35
# - SELL: close > upper band and RSI > 65
# ====================================================
def generate_signals_v4_bbands_meanrev(df):
    if df is None or len(df) < 25:
        return "-", None, None

    df = _ensure_cols(df, ["BB_MA_20", "BB_UPPER_20", "BB_LOWER_20", "ATR"])
    rsi = df["RSI"].iloc[-1]
    close = df["close"].iloc[-1]
    lower = df["BB_LOWER_20"].iloc[-1]
    upper = df["BB_UPPER_20"].iloc[-1]
    atr = df["ATR"].iloc[-1]

    if close < lower and rsi < 35:
        return "BUY", rsi, atr
    elif close > upper and rsi > 65:
        return "SELL", rsi, atr
    else:
        return "-", rsi, atr


# ====================================================
# v5: EMA cross + trend filter (trade with the trend only)
# - BUY: EMA9>EMA21 and close>EMA21 and RSI>45
# - SELL: EMA9<EMA21 and close<EMA21 and RSI<55
# (Filtering out counter-trend crosses reduces whipsaws)
# ====================================================
def generate_signals_v5_ema_cross_filter(df):
    if df is None or len(df) < 25:
        return "-", None, None

    df = _ensure_cols(df, ["EMA_9", "EMA_21", "ATR"])
    rsi = df["RSI"].iloc[-1]
    ema9 = df["EMA_9"].iloc[-1]
    ema21 = df["EMA_21"].iloc[-1]
    close = df["close"].iloc[-1]
    atr = df["ATR"].iloc[-1]

    # Uptrend only longs, downtrend only shorts
    if (ema9 > ema21) and (close > ema21) and (rsi > 45):
        return "BUY", rsi, atr
    elif (ema9 < ema21) and (close < ema21) and (rsi < 55):
        return "SELL", rsi, atr
    else:
        return "-", rsi, atr


# ====================================================
# v6: Donchian breakout (20) + ATR threshold
# - BUY: close > 20-bar high and (close - high_prev) > 0.2*ATR
# - SELL: close < 20-bar low  and (low_prev - close) > 0.2*ATR
# (ATR threshold filters out false breakouts)
# ====================================================
def generate_signals_v6_donchian_breakout(df, length=20, atr_thresh=0.2):
    if df is None or len(df) < length + 1:
        return "-", None, None

    df = _ensure_cols(df, ["DONCHIAN_UP_20", "DONCHIAN_LO_20", "ATR"])
    close = df["close"].iloc[-1]
    atr = df["ATR"].iloc[-1]
    rsi = df["RSI"].iloc[-1]

    # Channel bounds of the previous bar (a breakout is measured against the prior bar's channel)
    up_prev = df["DONCHIAN_UP_20"].iloc[-2]
    lo_prev = df["DONCHIAN_LO_20"].iloc[-2]

    # Is the move beyond the channel significant?
    if pd.notna(up_prev) and close > up_prev and (close - up_prev) > atr_thresh * atr:
        return "BUY", rsi, atr
    elif pd.notna(lo_prev) and close < lo_prev and (lo_prev - close) > atr_thresh * atr:
        return "SELL", rsi, atr
    else:
        return "-", rsi, atr


# ====================================================
# v7: Trend + RSI pullback/cross
# - Trend filter: EMA21 > EMA50 -> LONG only; EMA21 < EMA50 -> SHORT only
# - LONG: RSI dipped below 40, then crosses back above 45 (pullback is over)
# - SHORT: RSI rose above 60, then crosses back below 55
# ====================================================
def generate_signals_v7_trend_pullback_rsi(df):
    if df is None or len(df) < 55:
        return "-", None, None

    df = _ensure_cols(df, ["EMA_21", "EMA_50", "ATR"])
    rsi = df["RSI"].iloc[-1]
    rsi_prev = df["RSI"].iloc[-2]
    ema21 = df["EMA_21"].iloc[-1]
    ema50 = df["EMA_50"].iloc[-1]
    atr = df["ATR"].iloc[-1]

    # Uptrend: look for longs only
    if ema21 > ema50:
        # Pullback: RSI was below 40, then crossed above 45
        was_oversold = (df["RSI"].rolling(5).min().iloc[-2] < 40)  # below 40 within the last few candles
        cross_up = (rsi_prev < 45) and (rsi >= 45)
        if was_oversold and cross_up:
            return "BUY", rsi, atr

    # Downtrend: look for shorts only
    if ema21 < ema50:
        # Pullback: RSI was above 60, then crossed below 55
        was_overbought = (df["RSI"].rolling(5).max().iloc[-2] > 60)
        cross_down = (rsi_prev > 55) and (rsi <= 55)
        if was_overbought and cross_down:
            return "SELL", rsi, atr

    return "-", rsi, atr


# ==========================
# Strategy registry
# ==========================
# Every strategy takes an OHLCV DataFrame with indicators (see indicators.add_indicators)
# and returns (signal, rsi, atr), where signal is "BUY", "SELL" or "-".
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
