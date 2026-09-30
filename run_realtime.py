import ccxt
import pandas as pd
import time
import strategy
from paper_trader import PaperTrader
from indicators import add_indicators
import os
import csv

# ---- CONFIG ----
try:
    from config import SYMBOLS, STRATEGY
except Exception:
    SYMBOLS = ["BTC/USDT", "ETH/USDT"]
    STRATEGY = "v3"

try:
    from config import ATR_MULTIPLIER
except Exception:
    ATR_MULTIPLIER = 1.5

try:
    from config import TIMEFRAME
except Exception:
    TIMEFRAME = "5m"

symbols = SYMBOLS
timeframe = TIMEFRAME
limit = 100

binance = ccxt.binance({'enableRateLimit': True})
traders = {symbol: PaperTrader() for symbol in symbols}

def get_ohlcv(symbol):
    try:
        data = binance.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(data, columns=['timestamp','open','high','low','close','volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"Veri hatası ({symbol}): {e}")
        return None

def log_signal_to_csv(symbol, signal, rsi, price, position_status, position_opened, position_closed, stop_loss=None):
    filename = "signals_log.csv"
    file_exists = os.path.isfile(filename)
    with open(filename, mode="a", newline="") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow([
                "Timestamp","Symbol","Signal","RSI","Price",
                "Stop Loss","Position Status","Position Opened","Position Closed"
            ])
        w.writerow([
            pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
            symbol,
            signal,
            f"{rsi:.2f}" if rsi is not None else "",
            f"{price:.4f}",
            f"{stop_loss:.6f}" if stop_loss is not None else "",
            position_status,
            "YES" if position_opened else "NO",
            "YES" if position_closed else "NO"
        ])

def process_symbol(symbol):
    df = get_ohlcv(symbol)
    if df is None or len(df) < 30:
        return

    df = add_indicators(df)

    signal, rsi, atr = strategy.get_strategy(STRATEGY)(df)

    price = float(df['close'].iloc[-1])
    trader = traders[symbol]

    position_status = trader.status() or "NONE"
    position_opened = False
    position_closed = False

    # ATR tabanlı SL mesafesi
    candidate_sl_dist = (atr * ATR_MULTIPLIER) if (atr is not None and pd.notna(atr)) else None

    # Aktif pozisyon varsa SL kontrolü
    if trader.check_stop_loss(price):
        position_closed = True

    # Sinyal varsa logla + uygula
    if signal in ("BUY", "SELL"):
        sl_for_log = None
        if candidate_sl_dist is not None:
            sl_for_log = price - candidate_sl_dist if signal == "BUY" else price + candidate_sl_dist

        # Sinyali logla
        log_signal_to_csv(
            symbol, signal, rsi, price,
            position_status=position_status,
            position_opened=position_opened,
            position_closed=position_closed,
            stop_loss=sl_for_log
        )

        # Trade yönetimi
        if signal == "BUY":
            if not trader.status():
                trader.position_symbol = symbol
                trader.open_position("LONG", price, rsi, atr=candidate_sl_dist)
                if candidate_sl_dist is not None:
                    trader.stop_loss = price - candidate_sl_dist
                position_opened = True
            elif trader.status() == "SHORT":
                trader.close_position(price, reason="SIGNAL")
                position_closed = True

        elif signal == "SELL":
            if not trader.status():
                trader.position_symbol = symbol
                trader.open_position("SHORT", price, rsi, atr=candidate_sl_dist)
                if candidate_sl_dist is not None:
                    trader.stop_loss = price + candidate_sl_dist
                position_opened = True
            elif trader.status() == "LONG":
                trader.close_position(price, reason="SIGNAL")
                position_closed = True

    sl_show = f"{trader.stop_loss:.6f}" if trader.stop_loss is not None else "-"
    rsi_show = f"{rsi:.2f}" if rsi is not None else "-"
    print(f"[{symbol}] Sinyal: {signal or '-'} | RSI: {rsi_show} | Fiyat: {price:.4f} | SL: {sl_show}")

def main():
    print("📡 Realtime takip başlatıldı...\n")
    while True:
        for symbol in symbols:
            process_symbol(symbol)
            time.sleep(1.2)
        print("\n⏳ 1 dakika bekleniyor...\n")
        time.sleep(60)

if __name__ == "__main__":
    main()
