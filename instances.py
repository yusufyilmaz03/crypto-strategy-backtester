# instances.py
"""Named bot instances: each one is a strategy setup that runs as its own process.

Instances live in instances.json (gitignored). The dashboard creates, starts, stops
and deletes them; run_all.py starts a process for every enabled instance. A paper
instance stores its results in paper.db under its run key; a testnet instance has
its own database file. An instance's trading setup (strategy, parameters, timeframe,
symbols, stop) can't be changed after creation, so its results always describe one
setup; to try something else, create another instance.
"""
import inspect
import json
import os
import re
from dataclasses import asdict, dataclass, field

import pandas as pd

import config
import strategy
from settings import SYMBOL_RE

HERE = os.path.dirname(os.path.abspath(__file__))
INSTANCES_FILE = os.path.join(HERE, "instances.json")

MODES = ["paper", "testnet"]
TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h", "1d"]
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
SETUP_FIELDS = ["mode", "strategy", "params", "timeframe", "stop", "symbols", "equity"]


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
    symbols: list | None = None        # None = config.SYMBOLS
    equity: float = 1000.0             # paper: per symbol; testnet: total capital
    enabled: bool = False              # should run_all.py keep it running?
    run_key: str = ""                  # paper: key in paper.db; testnet: database file
    description: str = ""
    created_at: str = ""
    restart_requested_at: str | None = None

    @property
    def symbol_list(self):
        return list(self.symbols) if self.symbols else list(config.SYMBOLS)

    def engine_config(self):
        from dataclasses import replace
        return replace(config.engine_config(), atr_multiplier=config.STOPLOSS_CONFIG[self.stop],
                       initial_equity=self.equity)

    @property
    def lock_path(self):
        return f"{self.run_key}.lock" if self.mode == "testnet" else f"paper.db.{self.name}.lock"


def param_spec(key):
    """Tunable parameters of a strategy: [{"name", "default", "type"}] from its signature."""
    fn = strategy.get_strategy(key)
    spec = []
    for p in list(inspect.signature(fn).parameters.values())[1:]:
        if p.default is inspect.Parameter.empty:
            continue
        spec.append({"name": p.name, "default": p.default,
                     "type": "int" if isinstance(p.default, int) else "float"})
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
        raise InstanceError("name: 2-40 characters, lowercase letters, digits and '-'")
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
    return {d["name"]: Instance(**d) for d in raw}


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
    if inst.name in items:
        raise InstanceError(f"an instance named '{inst.name}' already exists")
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
        if k not in ("enabled", "description", "restart_requested_at"):
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
