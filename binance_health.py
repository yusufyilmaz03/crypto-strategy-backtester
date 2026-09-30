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
        'recvWindow': 20000  # tolerance for timestamp drift
    }
})

def main():
    print("== Binance Health Check ==")
    # 1) Ping (raw endpoint)
    t0 = binance.milliseconds()
    try:
        binance.publicGetPing()  # raw call; an error means network/access issues
        t1 = binance.milliseconds()
        print(f"Ping OK ~ {(t1 - t0)} ms")
    except Exception as e:
        print("Ping ERROR:", type(e).__name__, str(e))
        return

    # 2) Server time ve drift
    try:
        server_ms = binance.fetch_time()
        local_ms  = binance.milliseconds()
        drift = local_ms - server_ms
        print(f"Server time : {ms_to_iso(server_ms)}")
        print(f"Local  time : {ms_to_iso(local_ms)}")
        print(f"Clock drift : {drift} ms (positive = local clock ahead)")
        if abs(drift) > 2000:
            print("⚠️ Large clock drift. Sync the system clock or increase recvWindow.")
    except Exception as e:
        print("fetch_time ERROR:", type(e).__name__, str(e))

    # 3) Fetch a small OHLCV batch and measure latency
    symbol, tf = "BTC/USDT", "3m"
    try:
        t0 = binance.milliseconds()
        ohlcv = binance.fetch_ohlcv(symbol, timeframe=tf, limit=5)
        t1 = binance.milliseconds()
        print(f"OHLCV OK ({symbol},{tf}) ~ {(t1 - t0)} ms, last close: {ohlcv[-1][4]}")
    except Exception as e:
        print("fetch_ohlcv ERROR:", type(e).__name__, str(e))

if __name__ == "__main__":
    main()