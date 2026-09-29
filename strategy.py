# strategy.py
import pandas as pd
import numpy as np

# ==========================
# Yardımcı hesaplamalar
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
    """Gereken kolonlar yoksa yerinde üretmeye çalışır."""
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
# v1: Basit RSI + EMA kesişim
# ==========================
def generate_signals_v1(df):
    if df is None or len(df) < 21:
        return "-", None

    df = _ensure_cols(df, ["EMA_9", "EMA_21"])
    rsi = df["RSI"].iloc[-1]
    ema9 = df["EMA_9"].iloc[-1]
    ema21 = df["EMA_21"].iloc[-1]

    if rsi < 40 and ema9 > ema21:
        return "BUY", rsi
    elif rsi > 60 and ema9 < ema21:
        return "SELL", rsi
    else:
        return "-", rsi


# ==========================================
# v2: Son 3 mum ile filtrelenmiş RSI + EMA
# ==========================================
def generate_signals_v2(df):
    if df is None or len(df) < 25:
        return "-", None

    df = _ensure_cols(df, ["EMA_9", "EMA_21"])
    recent = df.iloc[-3:]  # son 3 mum

    buy_cond = (recent["RSI"] < 45) & (recent["EMA_9"] > recent["EMA_21"])
    sell_cond = (recent["RSI"] > 55) & (recent["EMA_9"] < recent["EMA_21"])

    rsi = df["RSI"].iloc[-1]

    if buy_cond.all():
        return "BUY", rsi
    elif sell_cond.all():
        return "SELL", rsi
    else:
        return "-", rsi


# =====================================
# v3: ATR stop-loss destekli strateji
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
# v4: Bollinger Mean-Reversion (BBANDS + RSI filtre)
# - Aşırı hareket → ortalamaya dönüş beklentisi
# - BUY: close < lower band ve RSI < 35
# - SELL: close > upper band ve RSI > 65
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
# v5: EMA Cross + Trend Filtresi (yalnız trend yönü)
# - BUY: EMA9>EMA21 ve close>EMA21 ve RSI>45
# - SELL: EMA9<EMA21 ve close<EMA21 ve RSI<55
# (Trend yönü dışında gelen çaprazları eleyerek whipsaw azaltma)
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
# v6: Donchian Breakout (20) + ATR eşiği
# - BUY: close > 20-bar high ve (close - high_prev) > 0.2*ATR
# - SELL: close < 20-bar low  ve (low_prev - close) > 0.2*ATR
# (Sahte kırılımları ATR ile filtreler)
# ====================================================
def generate_signals_v6_donchian_breakout(df, length=20, atr_thresh=0.2):
    if df is None or len(df) < length + 1:
        return "-", None, None

    df = _ensure_cols(df, ["DONCHIAN_UP_20", "DONCHIAN_LO_20", "ATR"])
    close = df["close"].iloc[-1]
    atr = df["ATR"].iloc[-1]
    rsi = df["RSI"].iloc[-1]

    # Önceki barın kanal sınırları (teknik olarak kırılım "geçen bar" referansı ile ölçülür)
    up_prev = df["DONCHIAN_UP_20"].iloc[-2]
    lo_prev = df["DONCHIAN_LO_20"].iloc[-2]

    # Kırılım sonrası hareket anlamlı mı?
    if pd.notna(up_prev) and close > up_prev and (close - up_prev) > atr_thresh * atr:
        return "BUY", rsi, atr
    elif pd.notna(lo_prev) and close < lo_prev and (lo_prev - close) > atr_thresh * atr:
        return "SELL", rsi, atr
    else:
        return "-", rsi, atr


# ====================================================
# v7: Trend + RSI Pullback/Cross
# - Trend filtresi: EMA21 > EMA50 ise yalnız LONG; EMA21 < EMA50 ise yalnız SHORT
# - LONG: RSI, 40 altına indikten sonra 45 üstüne geri CROSS yaparsa (pullback bitti sinyali)
# - SHORT: RSI, 60 üstüne çıktıktan sonra 55 altına geri CROSS yaparsa
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

    # Uptrend: sadece long arıyoruz
    if ema21 > ema50:
        # Pullback: önce RSI<40 olmuş olsun, sonra 45'i yukarı kesmiş olsun
        was_oversold = (df["RSI"].rolling(5).min().iloc[-2] < 40)  # son birkaç mumda 40 altı
        cross_up = (rsi_prev < 45) and (rsi >= 45)
        if was_oversold and cross_up:
            return "BUY", rsi, atr

    # Downtrend: sadece short arıyoruz
    if ema21 < ema50:
        # Pullback: önce RSI>60 olmuş olsun, sonra 55'i aşağı kesmiş olsun
        was_overbought = (df["RSI"].rolling(5).max().iloc[-2] > 60)
        cross_down = (rsi_prev > 55) and (rsi <= 55)
        if was_overbought and cross_down:
            return "SELL", rsi, atr

    return "-", rsi, atr
