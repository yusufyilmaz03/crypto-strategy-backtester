import time
import ccxt
import pandas as pd
from config import BINANCE_API_KEY, BINANCE_API_SECRET, SYMBOLS, TIMEFRAME
from strategy import generate_signals
from paper_trader import PaperTrader

binance = ccxt.binance({
    'apiKey': BINANCE_API_KEY,
    'secret': BINANCE_API_SECRET,
    'enableRateLimit': True
})

trader = PaperTrader()

def get_ohlcv(symbol):
    try:
        ohlcv = binance.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=20)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except Exception as e:
        print(f"Hata veri alırken ({symbol}): {e}")
        return None

if __name__ == "__main__":
    while True:
        try:
            if trader.status():  # Pozisyon açıksa sadece kapama sinyali ararız
                active_symbol = trader.position_symbol
                df = get_ohlcv(active_symbol)
                if df is None:
                    time.sleep(10)
                    continue

                signal = generate_signals(df)
                price = df['close'].iloc[-1]

                if (signal == "SELL" and trader.position == "LONG") or (signal == "BUY" and trader.position == "SHORT"):
                    trader.close_position(price)

            else:
                for symbol in SYMBOLS:
                    df = get_ohlcv(symbol)
                    if df is None:
                        continue

                    signal = generate_signals(df)
                    price = df['close'].iloc[-1]
                    print(f"[{symbol}] Fiyat: {price:.6f} | Sinyal: {signal or '-'}")

                    if signal == "BUY":
                        trader.position_symbol = symbol
                        trader.open_position("LONG", price)
                        break
                    elif signal == "SELL":
                        trader.position_symbol = symbol
                        trader.open_position("SHORT", price)
                        break

            time.sleep(10)

        except Exception as e:
            print(f"HATA: {e}")
            time.sleep(5)
