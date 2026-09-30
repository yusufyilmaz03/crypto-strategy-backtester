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
    "DOGE/USDT", "PEPE/USDT", "SHIB/USDT", "FLOKI/USDT",
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
    "LINK/USDT", "POL/USDT", "TRX/USDT", "APT/USDT", "SUI/USDT",
    "WIF/USDT", "ORDI/USDT", "TIA/USDT", "ARKM/USDT", "AVAX/USDT",
    "SEI/USDT", "RAY/USDT", "ADA/USDT", "XLM/USDT",
    "HBAR/USDT", "DOT/USDT", "ONDO/USDT", "AAVE/USDT",
    "PROVE/USDT", "TOWNS/USDT", "ILV/USDT", "EPIC/USDT",
    "LTC/USDT", "ATOM/USDT",
    "ARB/USDT", "OP/USDT", "NEAR/USDT", "ICP/USDT", "SAND/USDT",
    "APE/USDT", "RENDER/USDT", "S/USDT", "GALA/USDT"
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

# ===== Fee & slippage settings =====
# Spot taker fee rate (e.g. 0.1% = 0.001). Adjust to your actual exchange rate.
FEE_RATE_TAKER = 0.0010

# Slippage in basis points. 1 bps = 0.01%, e.g. 5 bps = 0.05% = 0.0005
SLIPPAGE_BPS = 5

# ===== Engine / evaluation settings =====
# Spot only allows long positions; shorts need margin or futures.
ALLOW_SHORT = False
# Each position uses the full equity (no leverage, compounding).
INITIAL_EQUITY = 1000.0  # quote currency (USDT) per symbol

# Backtest history and out-of-sample evaluation
BACKTEST_DAYS = 90
OOS_FRACTION = 0.3        # last 30% of the history is out-of-sample
WALK_FORWARD_FOLDS = 4    # test windows over the second half of the history (0 = off)


def engine_config():
    from engine import EngineConfig
    return EngineConfig(
        fee_rate=FEE_RATE_TAKER,
        slippage_bps=SLIPPAGE_BPS,
        atr_multiplier=ATR_MULTIPLIER,
        allow_short=ALLOW_SHORT,
        initial_equity=INITIAL_EQUITY,
    )


# ===== Strategy research (research.py) =====
# Timeframe -> days of history. Higher timeframes get longer histories so that each
# walk-forward window still holds enough trades.
RESEARCH_TIMEFRAMES = {"15m": 180, "1h": 365, "4h": 730}
WFO_FOLDS = 5
WFO_MIN_TRAIN_FRACTION = 0.4   # first 40% is only used for training
WFO_MIN_TRADES = 10            # min trades in a training window to trust its Sharpe
COST_STRESS = 1.5              # fees and slippage multiplier for the stress test
RESEARCH_MIN_CANDLES = 1500    # skip symbols with a shorter history
