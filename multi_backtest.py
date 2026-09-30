# multi_backtest.py
import ccxt
import pandas as pd
import numpy as np
from indicators import add_indicators
import strategy
import os
import csv

# --- config opsiyonelleri ---
try:
    from config import SYMBOLS
except Exception:
    SYMBOLS = ["BTC/USDT", "ETH/USDT", "BNB/USDT"]

try:
    from config import TIMEFRAMES
except Exception:
    TIMEFRAMES = ["1m", "3m", "5m"]

try:
    from config import STRATEGY_LIST
except Exception:
    # varsayılan; v1–v7 hepsini test etmek istersen burada listeyi genişlet
    STRATEGY_LIST = ["v1", "v2", "v3", "v4", "v5", "v6", "v7"]

try:
    from config import ATR_MULTIPLIER
except Exception:
    ATR_MULTIPLIER = 1.5

try:
    from config import FEE_RATE_TAKER, SLIPPAGE_BPS
except Exception:
    FEE_RATE_TAKER = 0.001
    SLIPPAGE_BPS   = 2

binance = ccxt.binance({'enableRateLimit': True})

def fetch_df(symbol, timeframe=TIMEFRAMES, limit=500):
    data = binance.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(data, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return add_indicators(df)

def apply_commission_slippage(entry_price, exit_price, side):
    """
    side: 'LONG' veya 'SHORT'
    Komisyonu her iki işlemde de uygula; slippage'ı giriş/çıkışta basit ekle.
    """
    # slippage (bps) -> oransal
    slip = SLIPPAGE_BPS / 10000.0
    if side == "LONG":
        adj_entry = entry_price * (1 + slip)
        adj_exit  = exit_price * (1 - slip)
        gross = adj_exit - adj_entry
    else:
        adj_entry = entry_price * (1 - slip)
        adj_exit  = exit_price * (1 + slip)
        gross = adj_entry - adj_exit

    # komisyon: her işlemde fiyata oransal kabul ediyoruz (al/sat ayrı ayrı)
    commission = (entry_price + exit_price) * FEE_RATE_TAKER
    net = gross - commission
    return net

def backtest_one(df: pd.DataFrame, strat_key: str):
    fn = strategy.get_strategy(strat_key)

    position = None
    entry_price = None
    entry_idx = None
    total_pnl = 0.0
    trade_count = 0

    # Stop-loss seviyesini sakla
    stop_loss = None

    # bar-bar simülasyon
    for i in range(max(30, 25), len(df)):
        row = df.iloc[i]
        sub = df.iloc[:i+1]  # sinyal hesaplamaları geçmişe bakar

        # Sinyal
        sig, rsi, atr = fn(sub)

        close = float(row["close"])
        high  = float(row["high"])
        low   = float(row["low"])

        # Açık pozisyon için SL kontrolü
        if position is not None and stop_loss is not None:
            if position == "LONG" and low <= stop_loss:
                pnl = apply_commission_slippage(entry_price, stop_loss, "LONG")
                total_pnl += pnl
                trade_count += 1
                position = None
                entry_price = None
                stop_loss = None
                # SL tetiklendiyse bu bar içinde yeni sinyal kullanmayalım
                continue
            elif position == "SHORT" and high >= stop_loss:
                pnl = apply_commission_slippage(entry_price, stop_loss, "SHORT")
                total_pnl += pnl
                trade_count += 1
                position = None
                entry_price = None
                stop_loss = None
                continue

        # Sinyal uygulaması
        if sig == "BUY":
            if position is None:
                position = "LONG"
                entry_price = close
                entry_idx = i
                stop_loss = (close - atr * ATR_MULTIPLIER) if (atr is not None and pd.notna(atr)) else None
            elif position == "SHORT":
                # Kapat ve LONG'a dönme basitliği: önce kapat (market close)
                pnl = apply_commission_slippage(entry_price, close, "SHORT")
                total_pnl += pnl
                trade_count += 1
                # yeni long aç
                position = "LONG"
                entry_price = close
                entry_idx = i
                stop_loss = (close - atr * ATR_MULTIPLIER) if (atr is not None and pd.notna(atr)) else None

        elif sig == "SELL":
            if position is None:
                position = "SHORT"
                entry_price = close
                entry_idx = i
                stop_loss = (close + atr * ATR_MULTIPLIER) if (atr is not None and pd.notna(atr)) else None
            elif position == "LONG":
                pnl = apply_commission_slippage(entry_price, close, "LONG")
                total_pnl += pnl
                trade_count += 1
                position = "SHORT"
                entry_price = close
                entry_idx = i
                stop_loss = (close + atr * ATR_MULTIPLIER) if (atr is not None and pd.notna(atr)) else None

        # aksi halde sinyal yok: bekle

    # isteğe bağlı: son pozisyonu kapat (close fiyatıyla)
    if position is not None and entry_price is not None:
        last_close = float(df["close"].iloc[-1])
        side = "LONG" if position == "LONG" else "SHORT"
        pnl = apply_commission_slippage(entry_price, last_close, side)
        total_pnl += pnl
        trade_count += 1

    return total_pnl, trade_count

def run():
    results = []
    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            print(f"⏳ Test başlıyor: {symbol} - {tf}")
            try:
                df = fetch_df(symbol, timeframe=tf, limit=800)
                for strat in STRATEGY_LIST:
                    total_pnl, trade_count = backtest_one(df, strat)
                    results.append({
                        "Symbol": symbol,
                        "Timeframe": tf,
                        "Strategy": strat,
                        "Total PnL": round(float(total_pnl), 6),
                        "Trade Count": int(trade_count)
                    })
            except Exception as e:
                print(f"[HATA] {symbol} {tf}: {e}")

    # CSV kaydet
    out_file = "multi_backtest_strategies.csv"
    df_out = pd.DataFrame(results)
    cols = ["Symbol", "Timeframe", "Strategy", "Total PnL", "Trade Count"]
    df_out = df_out[cols]
    df_out.to_csv(out_file, index=False)
    print("\n📊 Çoklu strateji backtest tamamlandı. En iyi sonuçlar:")
    print(df_out.sort_values(["Total PnL","Trade Count"], ascending=[False, False]).head(20))
    print(f"\n📁 Sonuçlar '{out_file}' olarak kaydedildi.")

if __name__ == "__main__":
    run()
