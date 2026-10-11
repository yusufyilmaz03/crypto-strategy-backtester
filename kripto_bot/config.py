import os

from kripto_bot import paths

# Optional: load variables from the project's .env file (pip install python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(paths.ROOT, ".env"))
except ImportError:
    pass

# API keys are optional: backtests and paper trading only use public market data.
# Never commit real keys. Put them in .env (see .env.example).
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
# Binance Spot Testnet keys (https://testnet.binance.vision), used by trading/run_testnet.py
BINANCE_TESTNET_API_KEY = os.getenv("BINANCE_TESTNET_API_KEY", "")
BINANCE_TESTNET_API_SECRET = os.getenv("BINANCE_TESTNET_API_SECRET", "")

# ===== Universe =====
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

# ===== Costs =====
# Spot taker fee rate (e.g. 0.1% = 0.001). Adjust to your actual exchange rate.
FEE_RATE_TAKER = 0.0010
# Slippage in basis points. 1 bps = 0.01%, e.g. 5 bps = 0.05% = 0.0005
SLIPPAGE_BPS = 5

# ===== Engine =====
# Spot only allows long positions; shorts need margin or futures.
ALLOW_SHORT = False
# Each position uses the full equity (no leverage, compounding).
INITIAL_EQUITY = 1000.0  # quote currency (USDT) per symbol

# ATR stop multipliers by name; "none" = exits come from signals only
STOPLOSS_CONFIG = {
    "tight": 1.0,
    "normal": 1.5,
    "loose": 2.0,
    "none": None,
}

# ===== Paper trading (trading/run_realtime.py) =====
# Forward test of the closest Phase 2b candidate (docs/forward-test.md):
# v9 moving-average regime on daily candles, long while close > SMA(50), no stop.
TIMEFRAME = "1d"
STRATEGY = "v9"
STRATEGY_PARAMS = {"fast": 1, "slow": 50}
PAPER_STOPLOSS_MODE = "none"

# ===== Order execution (trading/run_testnet.py) =====
# Uses the same strategy settings as paper trading (STRATEGY, STRATEGY_PARAMS, TIMEFRAME).
TRADING_MODE = "testnet"         # "testnet" or "live"
LIVE_TRADING_ENABLED = False     # must be set to True on purpose before any live order
TRADING_CAPITAL_USDT = 1000.0    # total budget, split equally across the traded symbols
MAX_ORDER_USDT = 100.0           # hard cap per order
DAILY_LOSS_LIMIT_PCT = 5.0       # no new entries for the rest of the UTC day below this loss
KILL_SWITCH_FILE = paths.runtime("KILL_SWITCH")  # if this file exists: no new entries; "flatten" in it closes all

# ===== Notifications (app/notify.py) =====
# Telegram messages from the supervisor. The bot token and chat id live in .env
# (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID); without them nothing is sent.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
NOTIFY_TRADES = True             # opened/closed positions and testnet orders
NOTIFY_DAILY_SUMMARY = True      # one summary of every instance per day
NOTIFY_SUMMARY_HOUR_UTC = 6      # when the daily summary is sent (06 UTC = 09:00 in Turkey)

# ===== Grid backtest (analysis/multi_backtest.py) =====
TIMEFRAMES = ["1m", "3m", "5m"]
STRATEGY_LIST = ["v1", "v2", "v3", "v4", "v5", "v6", "v7"]
STOPLOSS_MODE = "loose"
ATR_MULTIPLIER = STOPLOSS_CONFIG[STOPLOSS_MODE]
BACKTEST_DAYS = 90
OOS_FRACTION = 0.3        # last 30% of the history is out-of-sample
WALK_FORWARD_FOLDS = 4    # test windows over the second half of the history (0 = off)

# ===== Strategy research (analysis/research.py) =====
# Phase 2b set (docs/phase2b-preregistration.md). Phase 2 used 15m/1h/4h
# (180/365/730 days) with v1-v7 and stops {1.5, 2, 3}; none passed.
# Timeframe -> days of history.
RESEARCH_TIMEFRAMES = {"4h": 730, "1d": 1460}
RESEARCH_STRATEGIES = ["v8", "v9", "v10"]
RESEARCH_STOPS = [None, 2.0, 3.0]   # ATR multipliers; None = exit on signals only
ROTATION_TIMEFRAMES = ["1d"]        # cross-sectional rotation (R1)
HOLDOUT_FRACTION = 0.2              # last 20% of each timeframe is kept for the final test
# Holdout periods not used yet (the 1d holdout was spent on docs/v9-holdout-test.md).
# The playground leaves them out unless they are switched on explicitly.
UNUSED_HOLDOUTS = {"4h": "2026-05-07 19:31:35"}
PRIOR_TRIALS = 21                   # candidates tested in Phase 2, counted in the deflated Sharpe
WFO_FOLDS = 5
WFO_MIN_TRAIN_FRACTION = 0.4        # first 40% of the development data is only used for training
WFO_MIN_TRADES = 10                 # min trades in a training window to trust its Sharpe
COST_STRESS = 1.5                   # fees and slippage multiplier for the stress test
RESEARCH_MIN_CANDLES = 500          # skip symbols with a shorter development history


# ===== Dashboard overrides =====
# Values saved from the dashboard's settings page (settings.json) replace the defaults above.
from kripto_bot.settings import load_overrides as _load_overrides  # noqa: E402

globals().update(_load_overrides())


# ===== Helpers =====
def engine_config():
    """Engine settings for backtests and research (stop from STOPLOSS_MODE)."""
    from kripto_bot.core.engine import EngineConfig
    return EngineConfig(
        fee_rate=FEE_RATE_TAKER,
        slippage_bps=SLIPPAGE_BPS,
        atr_multiplier=ATR_MULTIPLIER,
        allow_short=ALLOW_SHORT,
        initial_equity=INITIAL_EQUITY,
    )


def paper_engine_config():
    """Engine settings for paper trading (stop from PAPER_STOPLOSS_MODE)."""
    from dataclasses import replace
    return replace(engine_config(), atr_multiplier=STOPLOSS_CONFIG[PAPER_STOPLOSS_MODE])


def strategy_label():
    """Name of the paper trading run: strategy, parameters and stop mode.
    Paper state is kept per label, so changing any of them starts a fresh run."""
    params = " ".join(f"{k}={v}" for k, v in STRATEGY_PARAMS.items())
    return " ".join(x for x in [STRATEGY, params, f"stop={PAPER_STOPLOSS_MODE}"] if x)
