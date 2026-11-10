# kripto_bot/paths.py (skill build)
"""Where the skill keeps its candle cache. Nothing else is written unless the
user passes --json. The command line's --data-dir replaces this default."""
import os

DATA_DIR = os.path.join(os.path.expanduser("~"), ".cache", "crypto-strategy-backtester", "ohlcv")
