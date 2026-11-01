# kripto_bot/instances.py
"""Named bot instances: each one is a strategy setup that runs as its own process.

Instances live in instances.json (gitignored). The dashboard creates, starts, stops
and deletes them; run_all.py starts a process for every enabled instance. A paper
instance stores its results in paper.db under its run key; a testnet instance has
its own database file. An instance's trading setup (strategy, parameters, timeframe,
symbols, stop, trailing exit,
lower-timeframe rules) can't be changed after creation, so its results always describe one
setup; to try something else, create another instance.
"""
import inspect
import json
import math
import os
import re
from dataclasses import asdict, dataclass, field

import pandas as pd

from kripto_bot import config, paths
from kripto_bot.core import bankroll, strategy
from kripto_bot.core.lower_timeframes import LOWER_TIMEFRAMES
from kripto_bot.settings import SYMBOL_RE

INSTANCES_FILE = paths.runtime("instances.json")

MODES = ["paper", "testnet"]
SIZINGS = {"equal": "Equal capital per symbol, never rebalanced",
           "inverse_vol": "Monthly rebalanced to 1/volatility weights (docs/v9-sizing-test.md)",
           "bankroll": "One bankroll for the instance: each BUY is scored and gets its grade's % of the "
                       "bankroll, strongest first, while cash lasts (docs/bankroll-management.md). Untested variant."}
TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h", "1d"]
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,59}$")
SETUP_FIELDS = ["mode", "strategy", "params", "timeframe", "stop", "trail", "symbols", "equity", "sizing",
                "lower_tf_entry", "lower_tf_exit", "lower_tf_sma", "trend_sma", "manual_close",
                "stakes", "min_entry", "max_entry", "score_inputs", "rotation", "rotate_profit_only", "rotate_trim"]
EDITABLE = ["enabled", "description", "restart_requested_at", "favorite", "low_basis", "low_pct"]
SMA_LENGTHS = (10, 250)    # trend filter and SMA mode; the paper bot fetches 300 candles


class InstanceError(ValueError):
    pass


@dataclass
class Instance:
    name: str
    mode: str = "paper"
    strategy: str = "v9"
    params: dict = field(default_factory=dict)
    timeframe: str = "1d"
    stop: str = "none"                 # key of config.STOPLOSS_CONFIG
    trail: float | None = None         # trailing exit at the highest close - ATR * trail; None = off
    symbols: list | None = None        # None = config.SYMBOLS
    equity: float = 1000.0             # paper: per symbol (bankroll: the starting bankroll); testnet: total capital
    sizing: str = "equal"              # key of SIZINGS
    lower_tf_entry: bool = False       # rule E: enter only if the lower timeframes say BUY too
    lower_tf_exit: bool = False        # rule X: exit inside the candle when they all say SELL
    lower_tf_sma: int | None = None    # E and X read close vs SMA(n) on the lower timeframes; None: the strategy's signal
    trend_sma: int | None = None       # trend filter: enter only above SMA(n) of the closes; None = off
    manual_close: bool = False         # positions can be closed from the panel (reason MANUAL)
    # sizing "bankroll" only (docs/bankroll-management.md)
    stakes: list | None = None         # % of the contributed capital per grade (Weak, Normal, Strong, Very strong)
    min_entry: float | None = None     # USDT; smaller stakes aren't bought
    max_entry: float | None = None     # USDT; stakes are capped here (None: no cap)
    score_inputs: list | None = None   # keys of bankroll.INPUTS
    rotation: bool = False             # a very strong signal without cash may sell weaker positions
    rotate_profit_only: bool = False   # ... only positions in profit
    rotate_trim: bool = False          # ... reducing them instead of closing when that is enough
    low_basis: str = "value"           # low-bankroll warning: key of bankroll.LOW_BASES (not part of the setup)
    low_pct: float = bankroll.DEFAULT_LOW_PCT  # ... below this % of the contributed capital
    enabled: bool = False              # should run_all.py keep it running?
    favorite: bool = False             # pinned on the panel; not part of the setup
    run_key: str = ""                  # paper: key in paper.db; testnet: database file in runtime/
    description: str = ""
    created_at: str = ""
    restart_requested_at: str | None = None

    @property
    def symbol_list(self):
        return list(self.symbols) if self.symbols else list(config.SYMBOLS)

    def engine_config(self):
        from dataclasses import replace
        return replace(config.engine_config(), atr_multiplier=config.STOPLOSS_CONFIG[self.stop],
                       trail_atr=self.trail, initial_equity=0.0 if self.sizing == "bankroll" else self.equity)

    def bankroll_config(self):
        """Settings of a bankroll instance (sizing "bankroll") for core/bankroll.py."""
        eng = config.engine_config()
        slow = None
        if self.strategy == "v9":       # its regime SMA; other strategies use the trend SMA
            slow = self.params.get("slow", {p["name"]: p["default"] for p in param_spec("v9")}.get("slow"))
        return bankroll.BankrollConfig(
            stakes=tuple(self.stakes or bankroll.DEFAULT_STAKES),
            min_entry=bankroll.DEFAULT_MIN_ENTRY if self.min_entry is None else self.min_entry,
            max_entry=self.max_entry, inputs=tuple(self.score_inputs or bankroll.DEFAULT_INPUTS),
            score_sma=int(slow or self.trend_sma or bankroll.SCORE_SMA),
            trend_sma=int(self.trend_sma or bankroll.TREND_SMA),
            rotation=self.rotation, profit_only=self.rotate_profit_only, trim=self.rotate_trim,
            fee_rate=eng.fee_rate, slippage_bps=eng.slippage_bps)

    @property
    def db_path(self):
        """Testnet: the instance's own database (run_key, relative to runtime/)."""
        return paths.runtime(self.run_key)


def param_spec(key):
    """Tunable parameters of a strategy: [{"name", "default", "type"}] from its signature."""
    fn = strategy.get_strategy(key)
    spec = []
    for p in list(inspect.signature(fn).parameters.values())[1:]:
        if p.default is inspect.Parameter.empty:
            continue
        spec.append({"name": p.name, "default": p.default,
                     "type": "int" if isinstance(p.default, int) else "float",
                     "description": strategy.PARAM_INFO.get(key, {}).get(p.name, "")})
    return spec


def _clean_params(key, params):
    spec = {p["name"]: p for p in param_spec(key)}
    out = {}
    for name, value in (params or {}).items():
        if name not in spec:
            raise InstanceError(f"{key} has no parameter '{name}'")
        if value in ("", None):
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise InstanceError(f"parameter {name}: '{value}' is not a number")
        if spec[name]["type"] == "int":
            if v != int(v):
                raise InstanceError(f"parameter {name} must be a whole number")
            v = int(v)
        if v < 0:
            raise InstanceError(f"parameter {name} must not be negative")
        out[name] = v
    return out


def validate(inst, existing):
    if not NAME_RE.match(inst.name):
        raise InstanceError("name: 2-60 characters: letters, digits, '-' and '_', starting with a letter or digit")
    if inst.mode not in MODES:
        raise InstanceError(f"mode must be one of {', '.join(MODES)}")
    if inst.strategy not in strategy.STRATEGY_DISPATCH:
        raise InstanceError(f"unknown strategy {inst.strategy}")
    inst.params = _clean_params(inst.strategy, inst.params)
    if inst.timeframe not in TIMEFRAMES:
        raise InstanceError(f"timeframe must be one of {', '.join(TIMEFRAMES)}")
    if inst.stop not in config.STOPLOSS_CONFIG:
        raise InstanceError(f"stop must be one of {', '.join(config.STOPLOSS_CONFIG)}")
    if inst.mode == "testnet" and config.STOPLOSS_CONFIG[inst.stop] is not None:
        raise InstanceError("testnet instances can't use a stop yet (exchange-side stop orders aren't implemented)")
    if inst.trail in ("", None):
        inst.trail = None
    else:
        try:
            inst.trail = float(inst.trail)
        except (TypeError, ValueError):
            raise InstanceError("trailing exit must be a number (ATR multiple) or empty")
        if not 0.5 <= inst.trail <= 20:
            raise InstanceError("trailing exit must be between 0.5 and 20 ATR")
        if inst.mode != "paper":
            raise InstanceError("only paper instances can use a trailing exit for now")
    for f in ("lower_tf_entry", "lower_tf_exit", "manual_close", "favorite", "rotation", "rotate_profit_only",
              "rotate_trim"):
        value = getattr(inst, f)
        if value in ("true", "false"):
            value = value == "true"
        if not isinstance(value, bool):
            raise InstanceError(f"{f} must be true or false")
        setattr(inst, f, value)
    if inst.lower_tf_entry or inst.lower_tf_exit:
        if inst.mode != "paper":
            raise InstanceError("only paper instances can use lower-timeframe rules for now")
        if inst.timeframe not in LOWER_TIMEFRAMES:
            raise InstanceError(f"lower-timeframe rules need a {' or '.join(LOWER_TIMEFRAMES)} timeframe")
    for f in ("lower_tf_sma", "trend_sma"):
        value = getattr(inst, f)
        if value in ("", None):
            setattr(inst, f, None)
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise InstanceError(f"{f} must be a whole number or empty")
        if value != int(value) or not SMA_LENGTHS[0] <= value <= SMA_LENGTHS[1]:
            raise InstanceError(f"{f} must be a whole number from {SMA_LENGTHS[0]} to {SMA_LENGTHS[1]}")
        setattr(inst, f, int(value))
    if inst.lower_tf_sma and not (inst.lower_tf_entry or inst.lower_tf_exit):
        raise InstanceError("the lower-timeframe SMA needs lower-timeframe entry or exit")
    if inst.trend_sma and inst.mode != "paper":
        raise InstanceError("only paper instances can use the trend filter for now")
    if inst.manual_close and inst.mode != "paper":
        raise InstanceError("only paper positions can be closed from the panel (testnet: use the kill switch)")
    if inst.sizing not in SIZINGS:
        raise InstanceError(f"sizing must be one of {', '.join(SIZINGS)}")
    if inst.sizing != "equal" and inst.mode != "paper":
        raise InstanceError("only paper instances can use portfolio sizing for now")
    _validate_bankroll(inst)
    if inst.symbols:
        inst.symbols = [s.strip().upper() for s in inst.symbols if s.strip()]
        bad = [s for s in inst.symbols if not SYMBOL_RE.match(s)]
        if bad:
            raise InstanceError(f"invalid symbols: {', '.join(bad)}")
    try:
        inst.equity = float(inst.equity)
    except (TypeError, ValueError):
        raise InstanceError("equity must be a number")
    if not 10 <= inst.equity <= 10_000_000:
        raise InstanceError("equity must be between 10 and 10,000,000 USDT")
    if inst.enabled and inst.mode == "testnet":
        others = [i.name for i in existing.values()
                  if i.mode == "testnet" and i.enabled and i.name != inst.name]
        if others:
            raise InstanceError(f"only one testnet instance can run at a time ({others[0]} is running): "
                                "they share one exchange account")
    return inst


def _number(value, what, low=None, high=None, empty_ok=True):
    if value in ("", None):
        if empty_ok:
            return None
        raise InstanceError(f"{what} is required")
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise InstanceError(f"{what} must be a number")
    if math.isnan(v) or (low is not None and v < low) or (high is not None and v > high):
        raise InstanceError(f"{what} must be from {low} to {high}" if high is not None else f"{what} must be at least {low}")
    return v


def _validate_bankroll(inst):
    """Bankroll settings: only with sizing "bankroll", which fills in their defaults."""
    inst.low_pct = _number(inst.low_pct, "low-bankroll threshold", 0, 100, empty_ok=False)
    if inst.low_basis not in bankroll.LOW_BASES:
        raise InstanceError(f"low-bankroll basis must be one of {', '.join(bankroll.LOW_BASES)}")
    if inst.sizing != "bankroll":
        used = [f for f in ("stakes", "min_entry", "max_entry", "score_inputs") if getattr(inst, f) not in (None, [], "")]
        used += [f for f in ("rotation", "rotate_profit_only", "rotate_trim") if getattr(inst, f)]
        if used:
            raise InstanceError(f"{', '.join(used)} need sizing 'bankroll'")
        inst.stakes = inst.min_entry = inst.max_entry = inst.score_inputs = None
        return
    stakes = bankroll.DEFAULT_STAKES if inst.stakes in (None, []) else inst.stakes
    if len(stakes) != len(bankroll.GRADES):
        raise InstanceError(f"stakes: one percentage for each grade ({', '.join(bankroll.GRADES)})")
    inst.stakes = [_number(v, f"stake for {g}", 0, 100, empty_ok=False) for v, g in zip(stakes, bankroll.GRADES)]
    if any(b < a for a, b in zip(inst.stakes, inst.stakes[1:])):
        raise InstanceError("stakes must not get smaller for a stronger grade")
    if not any(inst.stakes):
        raise InstanceError("at least one grade needs a stake above 0%")
    inst.min_entry = _number(inst.min_entry, "minimum entry", bankroll.MIN_ORDER)
    if inst.min_entry is None:
        inst.min_entry = bankroll.DEFAULT_MIN_ENTRY
    inst.max_entry = _number(inst.max_entry, "maximum entry", bankroll.MIN_ORDER)
    if inst.max_entry is not None and inst.max_entry < inst.min_entry:
        raise InstanceError("maximum entry must not be below the minimum entry")
    inputs = list(bankroll.DEFAULT_INPUTS) if inst.score_inputs in (None, []) else list(inst.score_inputs)
    bad = [i for i in inputs if i not in bankroll.INPUTS]
    if bad:
        raise InstanceError(f"unknown score inputs: {', '.join(map(str, bad))}")
    if "lower_tf" in inputs and inst.timeframe not in LOWER_TIMEFRAMES:
        raise InstanceError(f"the lower-timeframe score input needs a {' or '.join(LOWER_TIMEFRAMES)} timeframe")
    inst.score_inputs = [i for i in bankroll.INPUTS if i in inputs]       # canonical order, no repeats
    if (inst.rotate_profit_only or inst.rotate_trim) and not inst.rotation:
        raise InstanceError("the rotation options need rotation")


# ---------- storage ----------
def _default_instances():
    """First run: the existing forward test becomes 'forward-v9' (same paper.db key),
    plus a stopped testnet instance using the existing testnet.db."""
    now = pd.Timestamp.now(tz="UTC").isoformat()
    fwd = Instance(name="forward-v9", mode="paper", strategy=config.STRATEGY,
                   params=dict(config.STRATEGY_PARAMS), timeframe=config.TIMEFRAME,
                   stop=config.PAPER_STOPLOSS_MODE, equity=config.INITIAL_EQUITY, enabled=True,
                   run_key=config.strategy_label(), created_at=now,
                   description="Pre-registered forward test (docs/forward-test.md). Don't change.")
    tn = Instance(name="testnet-v9", mode="testnet", strategy=config.STRATEGY,
                  params=dict(config.STRATEGY_PARAMS), timeframe=config.TIMEFRAME,
                  stop=config.PAPER_STOPLOSS_MODE, equity=config.TRADING_CAPITAL_USDT, enabled=False,
                  run_key="testnet.db", created_at=now,
                  description="Order flow on the Binance Spot Testnet.")
    return {i.name: i for i in (fwd, tn)}


def load_all(path=None):
    path = path or INSTANCES_FILE
    if not os.path.exists(path):
        items = _default_instances()
        save_all(items, path)
        return items
    with open(path) as f:
        raw = json.load(f)
    known = Instance.__dataclass_fields__     # fields written by a newer version are ignored
    return {d["name"]: Instance(**{k: v for k, v in d.items() if k in known}) for d in raw}


def save_all(items, path=None):
    path = path or INSTANCES_FILE
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump([asdict(i) for i in items.values()], f, indent=2)
    os.replace(tmp, path)


def get(name, path=None):
    items = load_all(path)
    if name not in items:
        raise InstanceError(f"no instance named '{name}'")
    return items[name]


def create(data, path=None):
    items = load_all(path)
    inst = Instance(**{k: v for k, v in data.items() if k in Instance.__dataclass_fields__})
    # case-insensitive: log and lock files are named after the instance, and macOS file names ignore case
    same = [n for n in items if n.lower() == inst.name.lower()]
    if same:
        raise InstanceError(f"an instance named '{same[0]}' already exists")
    inst.created_at = pd.Timestamp.now(tz="UTC").isoformat()
    inst.run_key = f"testnet-{inst.name}.db" if inst.mode == "testnet" else f"instance:{inst.name}"
    validate(inst, items)
    items[inst.name] = inst
    save_all(items, path)
    return inst


def update(name, path=None, **changes):
    items = load_all(path)
    if name not in items:
        raise InstanceError(f"no instance named '{name}'")
    locked = [k for k in changes if k in SETUP_FIELDS]
    if locked:
        raise InstanceError(f"{', '.join(locked)} can't be changed after creation; create a new instance")
    inst = items[name]
    for k, v in changes.items():
        if k not in EDITABLE:
            raise InstanceError(f"field '{k}' can't be changed")
        setattr(inst, k, v)
    validate(inst, items)
    save_all(items, path)
    return inst


def delete(name, path=None):
    """Remove an instance from the list. Its stored results are kept."""
    items = load_all(path)
    if name not in items:
        raise InstanceError(f"no instance named '{name}'")
    if items[name].enabled:
        raise InstanceError("stop the instance before deleting it")
    del items[name]
    save_all(items, path)
