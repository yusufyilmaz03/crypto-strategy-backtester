import ccxt
import pandas as pd
import matplotlib.pyplot as plt
from strategy import generate_signals
from paper_trader import PaperTrader
from config import BINANCE_API_KEY, BINANCE_API_SECRET, TIMEFRAME

symbol = "DOGE/USDT"
limit = 500

binance = ccxt.binance({
    'apiKey': BINANCE_API_KEY,
    'secret': BINANCE_API_SECRET,
    'enableRateLimit': True
})

def get_historical_data():
    ohlcv = binance.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

def run_backtest(df):
    trader = PaperTrader()
    signals = []

    for i in range(20, len(df)):
        current_slice = df.iloc[:i+1]
        signal = generate_signals(current_slice)
        price = current_slice['close'].iloc[-1]
        timestamp = current_slice['timestamp'].iloc[-1]

        if signal == "BUY":
            if not trader.status():
                trader.position_symbol = symbol
                trader.open_position("LONG", price)
                signals.append((timestamp, price, "BUY"))
            elif trader.status() == "SHORT":
                trader.close_position(price)
                signals.append((timestamp, price, "EXIT"))

        elif signal == "SELL":
            if not trader.status():
                trader.position_symbol = symbol
                trader.open_position("SHORT", price)
                signals.append((timestamp, price, "SELL"))
            elif trader.status() == "LONG":
                trader.close_position(price)
                signals.append((timestamp, price, "EXIT"))

    if trader.status():
        trader.close_position(df['close'].iloc[-1])
        signals.append((df['timestamp'].iloc[-1], df['close'].iloc[-1], "EXIT"))

    return trader.trades, signals

def plot_chart(df, signals):
    plt.figure(figsize=(15,6))
    plt.plot(df['timestamp'], df['close'], label='Fiyat', color='blue')

    for timestamp, price, action in signals:
        if action == "BUY":
            plt.scatter(timestamp, price, marker='^', color='green', label='Buy', s=100)
        elif action == "SELL":
            plt.scatter(timestamp, price, marker='v', color='red', label='Sell', s=100)
        elif action == "EXIT":
            plt.scatter(timestamp, price, marker='o', color='orange', label='Exit', s=80)

    plt.title(f"{symbol} - Backtest Giriş/Çıkış Noktaları")
    plt.xlabel("Zaman")
    plt.ylabel("Fiyat (USDT)")
    plt.legend(loc="upper left")
    plt.grid()
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    df = get_historical_data()
    trades, signals = run_backtest(df)

    print("\n📊 İşlem Özeti:")
    total = 0
    for i, trade in enumerate(trades, 1):
        print(f"{i}. {trade['side']} | Entry: {trade['entry']:.6f} | Exit: {trade['exit']:.6f} | PnL: {trade['pnl']:.6f}")
        total += trade['pnl']

    print(f"\n🚀 Toplam Kar/Zarar: {total:.6f} USDT | Toplam İşlem Sayısı: {len(trades)}")

    plot_chart(df, signals)

    # CSV olarak işlemleri kaydet
    df_trades = pd.DataFrame(trades)
    df_trades.to_csv("backtest_trades.csv", index=False)
    print("\n📁 İşlem geçmişi 'backtest_trades.csv' olarak kaydedildi.")


