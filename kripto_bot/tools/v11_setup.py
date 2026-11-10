# kripto_bot/tools/v11_setup.py
"""Create the v11 test instances (docs/v11-ai-analyst.md) from a saved universe.

Four v11 instances share the symbols chosen on the start date: the primary setup, the two
secondary setups and the no-news control. A v9 paper instance on the same symbols is the v9
benchmark. Everything is created disabled unless --enable is given; enable right after a daily
close, so the v11 plans and v9's first candle start on the same day.

    python -m kripto_bot universe --n 20 --save            # on the start date
    python -m kripto_bot v11-setup --universe results/universe_<date>.json --group 5 [--enable]
"""
import argparse
import json

from kripto_bot import config, instances

SETUPS = [   # (instance, model, effort, news)
    ("v11-astra-high", "gpt-6-astra", "high", True),
    ("v11-astra-medium", "gpt-6-astra", "medium", True),
    ("v11-sol-high", "gpt-6.1-sol", "high", True),
    ("v11-astra-high-nonews", "gpt-6-astra", "high", False),
]
BENCHMARK = "v9-v11-symbols"
ROLES = {"v11-astra-high": "primary (pass/fail)", "v11-astra-medium": "secondary", "v11-sol-high": "secondary",
         "v11-astra-high-nonews": "no-news control"}


def create(symbols, group, equity=config.INITIAL_EQUITY, path=None, enable=False):
    """The five instances; returns their names."""
    names = []
    for name, model, effort, news in SETUPS:
        instances.create({"name": name, "strategy": instances.ANALYST, "timeframe": "1d", "symbols": symbols,
                          "equity": equity, "params": {"model": model, "effort": effort, "news": news, "group": group},
                          "description": f"v11 {ROLES[name]}: {model}, effort {effort}, news {'on' if news else 'off'}"},
                         path)
        names.append(name)
    instances.create({"name": BENCHMARK, "strategy": config.STRATEGY, "params": dict(config.STRATEGY_PARAMS),
                      "timeframe": "1d", "symbols": symbols, "equity": equity,
                      "description": "v9 on the v11 symbols: the v9 benchmark of docs/v11-ai-analyst.md"}, path)
    names.append(BENCHMARK)
    if enable:
        for name in names:
            instances.update(name, path, enabled=True)
    return names


def main():
    ap = argparse.ArgumentParser(description="Create the v11 test instances from a saved universe.")
    ap.add_argument("--universe", required=True, help="results/universe_<date>.json from `universe --save`")
    ap.add_argument("--group", type=int, required=True, help="symbols per scheduled call: 5, or 1 (dry-run choice)")
    ap.add_argument("--enable", action="store_true")
    a = ap.parse_args()
    with open(a.universe) as f:
        symbols = json.load(f)["symbols"]
    names = create(symbols, a.group, enable=a.enable)
    print(f"created {', '.join(names)} on {len(symbols)} symbols, group {a.group}, "
          f"{'enabled' if a.enable else 'disabled'}")


if __name__ == "__main__":
    main()
