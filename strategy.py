# strategy.py
"""Signal generators.

Every strategy takes an OHLCV DataFrame with indicators (see indicators.add_indicators)
plus optional keyword parameters, and returns a Series aligned with it holding "BUY",
"SELL" or "-" for each candle. The value at row i only depends on rows 0..i, so it
can be acted on once candle i has closed (tests/test_strategy.py checks this for
every strategy). Default parameters reproduce the original strategies.

With the long-only engine, SELL signals only close an open long position.
"""
import itertools

import numpy as np
import pandas as pd

from indicators import atr as _atr

# ==========================
# Helper calculations
# ==========================

def _ema(df: pd.DataFrame, length: int):
    """EMA of the close; reuses the EMA_<length> column when present."""
    col = f"EMA_{length}"
    if col in df.columns:
        return df[col]
    return df["close"].ewm(span=length, adjust=False).mean()

def _bbands(close: pd.Series, length: int = 20, mult: float = 2.0):
    ma = close.rolling(length).mean()
    std = close.rolling(length).std(ddof=0)
    return ma, ma + mult * std, ma - mult * std

def _donchian(df: pd.DataFrame, length: int = 20):
    return df["high"].rolling(length).max(), df["low"].rolling(length).min()

def _atr_col(df):
    return df["ATR"] if "ATR" in df.columns else _atr(df, 14)

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
def generate_signals_v1(df, rsi_buy=40, rsi_sell=60, fast=9, slow=21):
    rsi, ema_f, ema_s = df["RSI"], _ema(df, fast), _ema(df, slow)
    buy = (rsi < rsi_buy) & (ema_f > ema_s)
    sell = (rsi > rsi_sell) & (ema_f < ema_s)
    return _to_signals(df, buy, sell, warmup=max(20, slow - 1))


# ==========================================
# v2: RSI + EMA, confirmed on the last `confirm` candles
# ==========================================
def generate_signals_v2(df, rsi_buy=45, rsi_sell=55, confirm=3, fast=9, slow=21):
    rsi, ema_f, ema_s = df["RSI"], _ema(df, fast), _ema(df, slow)
    buy_now = ((rsi < rsi_buy) & (ema_f > ema_s)).astype(float)
    sell_now = ((rsi > rsi_sell) & (ema_f < ema_s)).astype(float)
    buy = buy_now.rolling(confirm).min() == 1
    sell = sell_now.rolling(confirm).min() == 1
    return _to_signals(df, buy, sell, warmup=max(24, slow - 1))


# =====================================
# v3: RSI + EMA with looser thresholds
# =====================================
def generate_signals_v3(df, rsi_buy=45, rsi_sell=55, fast=9, slow=21):
    rsi, ema_f, ema_s = df["RSI"], _ema(df, fast), _ema(df, slow)
    buy = (rsi < rsi_buy) & (ema_f > ema_s)
    sell = (rsi > rsi_sell) & (ema_f < ema_s)
    return _to_signals(df, buy, sell, warmup=max(14, slow - 1))


# ====================================================
# v4: Bollinger mean reversion (BBANDS + RSI filter)
# - Overextended move -> expect reversion to the mean
# - BUY: close < lower band and RSI < rsi_buy
# - SELL: close > upper band and RSI > rsi_sell
# ====================================================
def generate_signals_v4_bbands_meanrev(df, bb_len=20, bb_mult=2.0, rsi_buy=35, rsi_sell=65):
    rsi, close = df["RSI"], df["close"]
    _, upper, lower = _bbands(close, bb_len, bb_mult)
    buy = (close < lower) & (rsi < rsi_buy)
    sell = (close > upper) & (rsi > rsi_sell)
    return _to_signals(df, buy, sell, warmup=max(24, bb_len - 1))


# ====================================================
# v5: EMA cross + trend filter (trade with the trend only)
# - BUY: EMAfast>EMAslow and close>EMAslow and RSI>rsi_buy
# - SELL: EMAfast<EMAslow and close<EMAslow and RSI<rsi_sell
# (Filtering out counter-trend crosses reduces whipsaws)
# ====================================================
def generate_signals_v5_ema_cross_filter(df, fast=9, slow=21, rsi_buy=45, rsi_sell=55):
    rsi, close, ema_f, ema_s = df["RSI"], df["close"], _ema(df, fast), _ema(df, slow)
    buy = (ema_f > ema_s) & (close > ema_s) & (rsi > rsi_buy)
    sell = (ema_f < ema_s) & (close < ema_s) & (rsi < rsi_sell)
    return _to_signals(df, buy, sell, warmup=max(24, slow - 1))


# ====================================================
# v6: Donchian breakout + ATR threshold
# - BUY: close > previous N-bar high by more than atr_thresh*ATR
# - SELL: close < previous N-bar low by more than atr_thresh*ATR
# (ATR threshold filters out false breakouts)
# ====================================================
def generate_signals_v6_donchian_breakout(df, length=20, atr_thresh=0.2):
    close, atr = df["close"], _atr_col(df)
    up, lo = _donchian(df, length)
    # Channel bounds of the previous bar (a breakout is measured against the prior bar's channel)
    up_prev, lo_prev = up.shift(1), lo.shift(1)
    buy = (close > up_prev) & ((close - up_prev) > atr_thresh * atr)
    sell = (close < lo_prev) & ((lo_prev - close) > atr_thresh * atr)
    return _to_signals(df, buy, sell, warmup=max(20, length))


# ====================================================
# v7: Trend + RSI pullback/cross
# - Trend filter: EMA trend_fast > EMA trend_slow -> LONG only; below -> SHORT only
# - LONG: RSI dipped below `oversold`, then crosses back above `buy_cross`
# - SHORT: RSI rose above `overbought`, then crosses back below `sell_cross`
# ====================================================
def generate_signals_v7_trend_pullback_rsi(df, trend_fast=21, trend_slow=50, oversold=40,
                                           buy_cross=45, overbought=60, sell_cross=55, lookback=5):
    rsi = df["RSI"]
    rsi_prev = rsi.shift(1)
    ema_f, ema_s = _ema(df, trend_fast), _ema(df, trend_slow)
    uptrend, downtrend = ema_f > ema_s, ema_f < ema_s
    # RSI was below oversold / above overbought within the `lookback` candles before this one
    was_oversold = rsi.rolling(lookback).min().shift(1) < oversold
    was_overbought = rsi.rolling(lookback).max().shift(1) > overbought
    buy = uptrend & was_oversold & (rsi_prev < buy_cross) & (rsi >= buy_cross)
    sell = downtrend & was_overbought & (rsi_prev > sell_cross) & (rsi <= sell_cross)
    return _to_signals(df, buy, sell, warmup=max(54, trend_slow + 4))


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

# Parameter grids for walk-forward optimization. Kept small on purpose: every extra
# combination is another chance to fit noise. Tuples of names vary together.
PARAM_GRIDS = {
    "v1": {"rsi_buy": [30, 35, 40, 45], "rsi_sell": [55, 60, 65, 70]},
    "v2": {"rsi_buy": [40, 45, 50], "rsi_sell": [50, 55, 60], "confirm": [2, 3]},
    "v3": {"rsi_buy": [40, 45, 50], "rsi_sell": [50, 55, 60]},
    "v4": {"bb_mult": [1.5, 2.0, 2.5], "rsi_buy": [25, 30, 35, 40], "rsi_sell": [60, 65, 70]},
    "v5": {("fast", "slow"): [(9, 21), (12, 26), (20, 50)], "rsi_buy": [40, 45, 50, 55],
           "rsi_sell": [45, 55]},
    "v6": {"length": [10, 20, 40, 55], "atr_thresh": [0.0, 0.2, 0.5]},
    "v7": {("trend_fast", "trend_slow"): [(21, 50), (50, 200)], "oversold": [30, 35, 40],
           "buy_cross": [45, 50]},
}


def get_strategy(key):
    """Return the signal function registered under `key`."""
    if key not in STRATEGY_DISPATCH:
        raise ValueError(f"Unknown strategy: {key}")
    return STRATEGY_DISPATCH[key]


def param_combinations(key):
    """All parameter dicts in the strategy's grid ({} if it has none)."""
    grid = PARAM_GRIDS.get(key, {})
    names, values = list(grid), list(grid.values())
    combos = []
    for choice in itertools.product(*values):
        params = {}
        for name, value in zip(names, choice):
            if isinstance(name, tuple):
                params.update(zip(name, value))
            else:
                params[name] = value
        combos.append(params)
    return combos or [{}]
