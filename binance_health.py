# binance_health.py
import ccxt, time
from datetime import datetime, timezone

def ms_to_iso(ms): 
    return datetime.fromtimestamp(ms/1000, tz=timezone.utc).isoformat()

binance = ccxt.binance({
    'enableRateLimit': True,
    'timeout': 20000,  # 20s
    'options': {
        'defaultType': 'spot',
        'recvWindow': 20000  # timestamp drift toleransı
    }
})

def main():
    print("== Binance Sağlık Testi ==")
    # 1) Ping (ham endpoint)
    t0 = binance.milliseconds()
    try:
        binance.publicGetPing()  # ham çağrı; hata verirse ağ/erişim
        t1 = binance.milliseconds()
        print(f"Ping OK ~ {(t1 - t0)} ms")
    except Exception as e:
        print("Ping HATASI:", type(e).__name__, str(e))
        return

    # 2) Server time ve drift
    try:
        server_ms = binance.fetch_time()
        local_ms  = binance.milliseconds()
        drift = local_ms - server_ms
        print(f"Server time : {ms_to_iso(server_ms)}")
        print(f"Local  time : {ms_to_iso(local_ms)}")
        print(f"Clock drift : {drift} ms (pozitifse local daha ileri)")
        if abs(drift) > 2000:
            print("⚠️ Saat farkı büyük. Sistem saatini senkronize et veya recvWindow’u büyüt.")
    except Exception as e:
        print("fetch_time HATASI:", type(e).__name__, str(e))

    # 3) Basit OHLCV çekip latency ölç
    symbol, tf = "BTC/USDT", "3m"
    try:
        t0 = binance.milliseconds()
        ohlcv = binance.fetch_ohlcv(symbol, timeframe=tf, limit=5)
        t1 = binance.milliseconds()
        print(f"OHLCV OK ({symbol},{tf}) ~ {(t1 - t0)} ms, son close: {ohlcv[-1][4]}")
    except Exception as e:
        print("fetch_ohlcv HATASI:", type(e).__name__, str(e))

if __name__ == "__main__":
    main()