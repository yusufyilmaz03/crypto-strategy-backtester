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
TIMEFRAME = "3m"  # overridden below for the paper forward test

STRATEGY_LIST = [
    "v1",
    "v2",
    "v3",
    "v4",
    "v5",
    "v6",
    "v7"
]
STRATEGY = "v2"  # overridden below for the paper forward test

STOPLOSS_CONFIG = {
    "tight": 1.0,
    "normal": 1.5,
    "loose": 2.0,
    "none": None,   # no ATR stop; exits come from signals only
}
STOPLOSS_MODE = "loose"
ATR_MULTIPLIER = STOPLOSS_CONFIG[STOPLOSS_MODE]

# ===== Paper trading (run_realtime.py) =====
# Forward test of the closest Phase 2b candidate (docs/forward-test.md):
# v9 moving-average regime on daily candles, long while close > SMA(50), no stop.
TIMEFRAME = "1d"
STRATEGY = "v9"
STRATEGY_PARAMS = {"fast": 1, "slow": 50}
PAPER_STOPLOSS_MODE = "none"   # backtests keep STOPLOSS_MODE above


def strategy_label():
    """Name of the paper trading run: strategy, parameters and stop mode.
    Paper state is kept per label, so changing any of them starts a fresh run."""
    params = " ".join(f"{k}={v}" for k, v in STRATEGY_PARAMS.items())
    return " ".join(x for x in [STRATEGY, params, f"stop={PAPER_STOPLOSS_MODE}"] if x)


def paper_engine_config():
    from dataclasses import replace
    return replace(engine_config(), atr_multiplier=STOPLOSS_CONFIG[PAPER_STOPLOSS_MODE])

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
# Phase 2b set (docs/phase2b-preregistration.md). Phase 2 used 15m/1h/4h
# (180/365/730 days) with v1-v7 and stops {1.5, 2, 3}; none passed.
# Timeframe -> days of history.
RESEARCH_TIMEFRAMES = {"4h": 730, "1d": 1460}
RESEARCH_STRATEGIES = ["v8", "v9", "v10"]
RESEARCH_STOPS = [None, 2.0, 3.0]   # ATR multipliers; None = exit on signals only
ROTATION_TIMEFRAMES = ["1d"]        # cross-sectional rotation (R1)
HOLDOUT_FRACTION = 0.2              # last 20% of each timeframe is kept for the final test
PRIOR_TRIALS = 21                   # candidates tested in Phase 2, counted in the deflated Sharpe
WFO_FOLDS = 5
WFO_MIN_TRAIN_FRACTION = 0.4        # first 40% of the development data is only used for training
WFO_MIN_TRADES = 10                 # min trades in a training window to trust its Sharpe
COST_STRESS = 1.5                   # fees and slippage multiplier for the stress test
RESEARCH_MIN_CANDLES = 500          # skip symbols with a shorter development history
