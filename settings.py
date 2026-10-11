# settings.py
"""Settings that can be changed from the dashboard instead of editing config.py.

config.py keeps the defaults. Values saved from the dashboard go to settings.json
(gitignored) and override those defaults when config is imported. Every editable
setting is described in SCHEMA (type, limits, help text, which services need a
restart). Secrets never go through here: API keys stay in .env, and settings marked
read-only (for example LIVE_TRADING_ENABLED) can only be changed in config.py.
"""
import json
import os
import re
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "settings.json")

SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,15}/[A-Z0-9]{2,10}$")


@dataclass
class Setting:
    key: str
    group: str
    label: str
    kind: str                      # "float" | "int" | "choice" | "symbols" | "bool"
    help: str = ""
    min: float | None = None
    max: float | None = None
    choices: list = field(default_factory=list)
    restart: list = field(default_factory=list)   # services affected: "paper", "testnet", "dashboard"
    editable: bool = True


SCHEMA = [
    # Costs
    Setting("FEE_RATE_TAKER", "Costs", "Taker fee rate", "float",
            "Fee per fill as a fraction (0.001 = 0.1%). Used by backtests and paper trading.",
            0.0, 0.01, restart=["paper"]),
    Setting("SLIPPAGE_BPS", "Costs", "Slippage (bps)", "float",
            "Adverse slippage per fill in basis points (1 bps = 0.01%).", 0, 100, restart=["paper"]),
    # Universe
    Setting("SYMBOLS", "Universe", "Symbols", "symbols",
            "Default symbol list (BASE/QUOTE). Instances use it unless they define their own.",
            restart=["paper", "testnet"]),
    # Paper / engine
    Setting("INITIAL_EQUITY", "Paper trading", "Default equity per symbol (USDT)", "float",
            "Starting paper equity per symbol for new instances.", 10, 1_000_000),
    Setting("ALLOW_SHORT", "Paper trading", "Allow short positions", "bool",
            "Spot markets can't be shorted, so this stays off.", editable=False),
    # Order execution
    Setting("TRADING_CAPITAL_USDT", "Order execution", "Trading capital (USDT)", "float",
            "Total budget of the testnet instance, split equally across its symbols.", 10, 10_000_000,
            restart=["testnet"]),
    Setting("MAX_ORDER_USDT", "Order execution", "Max order size (USDT)", "float",
            "Hard cap per order.", 5, 1_000_000, restart=["testnet"]),
    Setting("DAILY_LOSS_LIMIT_PCT", "Order execution", "Daily loss limit (%)", "float",
            "After this loss within a UTC day no new entries are made until the next day.", 0.5, 50,
            restart=["testnet"]),
    Setting("TRADING_MODE", "Order execution", "Trading mode", "choice",
            "Only the testnet can be used from the dashboard.", choices=["testnet", "live"], editable=False),
    Setting("LIVE_TRADING_ENABLED", "Order execution", "Live trading enabled", "bool",
            "Can only be switched on in config.py, on purpose.", editable=False),
    # Backtest / research
    Setting("BACKTEST_DAYS", "Backtest & research", "Grid backtest history (days)", "int",
            "History length used by multi_backtest.py.", 7, 3650),
    Setting("OOS_FRACTION", "Backtest & research", "Out-of-sample fraction", "float",
            "Share of the history kept out-of-sample in multi_backtest.py.", 0.05, 0.9),
    Setting("WFO_FOLDS", "Backtest & research", "Walk-forward folds", "int",
            "Number of walk-forward test windows in research.py.", 2, 20),
    Setting("WFO_MIN_TRADES", "Backtest & research", "Min trades per training window", "int",
            "Minimum trades before a training window's Sharpe is trusted.", 1, 1000),
    Setting("COST_STRESS", "Backtest & research", "Cost stress multiplier", "float",
            "Fees and slippage multiplier for the stress test.", 1.0, 5.0),
]
BY_KEY = {s.key: s for s in SCHEMA}


class SettingsError(ValueError):
    pass


def coerce(setting, value):
    """Convert a submitted value to the setting's type and check its limits."""
    k = setting.kind
    try:
        if k == "float":
            v = float(value)
        elif k == "int":
            v = int(float(value))
            if float(value) != v:
                raise ValueError
        elif k == "bool":
            v = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
        elif k == "choice":
            v = str(value)
            if v not in setting.choices:
                raise SettingsError(f"{setting.label}: must be one of {', '.join(setting.choices)}")
        elif k == "symbols":
            items = value if isinstance(value, list) else re.split(r"[\s,]+", str(value))
            v = []
            for s in (x.strip().upper() for x in items):
                if not s:
                    continue
                if not SYMBOL_RE.match(s):
                    raise SettingsError(f"{setting.label}: '{s}' is not a BASE/QUOTE symbol")
                if s not in v:
                    v.append(s)
            if not v:
                raise SettingsError(f"{setting.label}: at least one symbol is required")
        else:
            raise SettingsError(f"unknown kind {k}")
    except SettingsError:
        raise
    except (TypeError, ValueError):
        raise SettingsError(f"{setting.label}: invalid value {value!r}")
    if k in ("float", "int"):
        if setting.min is not None and v < setting.min:
            raise SettingsError(f"{setting.label}: must be at least {setting.min}")
        if setting.max is not None and v > setting.max:
            raise SettingsError(f"{setting.label}: must be at most {setting.max}")
    return v


def load_overrides(path=SETTINGS_FILE):
    """Saved overrides for editable settings; invalid or unknown entries are ignored."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for key, value in raw.items():
        s = BY_KEY.get(key)
        if s is None or not s.editable:
            continue
        try:
            out[key] = coerce(s, value)
        except SettingsError:
            continue
    return out


def save(changes, path=SETTINGS_FILE):
    """Validate and store changes. Returns (saved overrides, services needing a restart)."""
    current = load_overrides(path)
    restart = set()
    for key, value in changes.items():
        s = BY_KEY.get(key)
        if s is None:
            raise SettingsError(f"unknown setting {key}")
        if not s.editable:
            raise SettingsError(f"{s.label} can't be changed from the dashboard")
        v = coerce(s, value)
        if current.get(key) != v:
            restart.update(s.restart)
        current[key] = v
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(current, f, indent=2)
    os.replace(tmp, path)
    return current, sorted(restart)


def reset(key, path=SETTINGS_FILE):
    """Remove an override so the config.py default applies again."""
    current = load_overrides(path)
    current.pop(key, None)
    with open(path, "w") as f:
        json.dump(current, f, indent=2)
