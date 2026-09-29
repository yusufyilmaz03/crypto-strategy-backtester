import os

# Optional: load variables from a local .env file (pip install python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# API keys are optional: backtests and paper trading only use public market data.
# Never commit real keys. Put them in .env (see .env.example).
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
SYMBOLS = [
    "DOGE/USDT", "1000PEPE/USDT", "SHIB/USDT", "FLOKI/USDT",
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
    "LINK/USDT", "MATIC/USDT", "TRX/USDT", "APT/USDT", "SUI/USDT",
    "WIF/USDT", "ORDI/USDT", "TIA/USDT", "ARKM/USDT", "AVAX/USDT",
    "SEI/USDT", "RAY/USDT", "MYX/USDT", "ADA/USDT", "XLM/USDT",
    "TON/USDT", "HBAR/USDT", "DOT/USDT", "ONDO/USDT", "AAVE/USDT",
    "PROVE/USDT", "TOWNS/USDT", "PYR/USDT", "ILV/USDT", "EPIC/USDT",
    "AVAX/USDT", "LTC/USDT", "ATOM/USDT", "APT/USDT",
    "ARB/USDT", "OP/USDT", "NEAR/USDT", "ICP/USDT", "SAND/USDT",
    "APE/USDT", "RNDR/USDT", "FTM/USDT", "GALA/USDT"
]

TIMEFRAMES = ["1m", "3m", "5m"]
TIMEFRAME = "3m"

STRATEGY_LIST = [
    "v1",
    "v2",
    "v3",
    "v4",
    "v5",
    "v6",
    "v7"
]
STRATEGY = "v2"

STOPLOSS_CONFIG = {
    "tight": 1.0,
    "normal": 1.5,
    "loose": 2.0
}
STOPLOSS_MODE = "loose"
ATR_MULTIPLIER = STOPLOSS_CONFIG[STOPLOSS_MODE]

# ===== Ücret & Slippage Ayarları =====
# Spot taker fee oranı (örn. %0.1 = 0.001). İstersen borsadaki gerçek orana göre değiştir.
FEE_RATE_TAKER = 0.0010

# Slippage (basis points). 1 bps = %0.01. Örn. 5 bps = %0.05 = 0.0005
SLIPPAGE_BPS = 5

# (Opsiyonel) pozisyon büyüklüğü (şimdilik 1 birimle çalışıyoruz)
POSITION_SIZE = 1.0