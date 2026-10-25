# kripto_bot/analysis/exits.py
"""Exit variants of v9 vs the baseline (docs/v9-exit-test.md).

Same point-in-time top-50 universes, equal weights and costs as the survivorship test;
only the trading rules differ: a trailing exit (chandelier, highest close since entry
minus k × ATR) or an entry band (close above SMA50 × (1 + b) to enter).

    python -m kripto_bot exits
"""
import os
from dataclasses import replace
from multiprocessing import Pool

import numpy as np
import pandas as pd

from kripto_bot import config, paths
from kripto_bot.analysis import survivorship as sv
from kripto_bot.core import archive

TRAIL = 3.0                         # primary ATR multiple of variant T
BAND = 0.02                         # primary entry band of variant H
TRAIL_NEIGHBORS = [2.0, 3.0, 4.0, 5.0]     # reported only
BAND_NEIGHBORS = [0.01, 0.02, 0.03, 0.05]  # reported only
BASELINE = "baseline"
PRIMARY = {"T": f"trail {TRAIL:g}xATR", "H": f"band {BAND:.0%}"}


def variants():
    """{name: (v9 params, trailing ATR multiple)}: baseline, both neighborhoods, the combination."""
    out = {BASELINE: ({}, None)}
    out.update({f"trail {k:g}xATR": ({}, k) for k in TRAIL_NEIGHBORS})
    out.update({f"band {b:.0%}": ({"entry_band": b}, None) for b in BAND_NEIGHBORS})
    out[f"trail {TRAIL:g}xATR + band {BAND:.0%}"] = ({"entry_band": BAND}, TRAIL)
    return out


def _job(job):
    asset, df, name, costs, params, trail, cfg = job
    cfg = replace(cfg, trail_atr=trail)
    return (name, costs, asset), sv.strategy_sleeve(df, "v9", {"fast": 1, "slow": 50, **params}, cfg)


def run(assets, n=50, workers=None, log=print):
    base = replace(config.engine_config(), atr_multiplier=None, allow_short=False)
    stress = replace(base, fee_rate=base.fee_rate * config.COST_STRESS,
                     slippage_bps=base.slippage_bps * config.COST_STRESS)
    cfgs = {"base": base, "x1.5": stress}
    universes = {p: sv.universe(assets, sv.month_starts(*sv.PERIODS[p]), n) for p in ("B", "A")}
    used = sorted({a for u in universes.values() for ms in u.values() for a in ms})
    log(f"{len(used)} assets in the universes")
    runs = [(name, "base", *v) for name, v in variants().items()]
    runs += [(name, "x1.5", *variants()[name]) for name in (BASELINE, *PRIMARY.values())]
    jobs = [(a, assets[a], name, costs, params, trail, cfgs[costs])
            for name, costs, params, trail in runs for a in used]
    sleeves = {}
    with Pool(workers or max(1, (os.cpu_count() or 2) - 1)) as pool:
        for k, (key, sl) in enumerate(pool.imap_unordered(_job, jobs, chunksize=8), 1):
            sleeves[key] = sl
            if k % 1000 == 0:
                log(f"  {k}/{len(jobs)} backtests")
    rows, curves = [], {}
    for p, members in universes.items():
        for name, costs, _, _ in runs:
            values, expo, entries = sv.portfolio({a: sleeves[(name, costs, a)] for a in used}, members,
                                                 sv.PERIODS[p][1], sv._fill_cost(cfgs[costs]))
            rows.append({"period": p, "variant": name, "costs": costs, **sv.stats(values),
                         "exposure_pct": expo * 100, "entries": entries})
            curves[(p, name, costs)] = values
    return pd.DataFrame(rows), curves


def verdict(table):
    """{variant key: {criterion: bool}} for the pre-registered criteria."""
    def row(p, name, costs="base"):
        t = table
        return t[(t.period == p) & (t.variant == name) & (t.costs == costs)].iloc[0]
    out = {}
    for key, name in PRIMARY.items():
        checks = {}
        for p in ("B", "A"):
            v, b = row(p, name), row(p, BASELINE)
            checks[f"Sharpe above the baseline ({p})"] = v.sharpe > b.sharpe
            checks[f"max drawdown not above the baseline ({p})"] = v.max_dd_pct <= b.max_dd_pct
        for p in ("B", "A"):
            checks[f"Sharpe above the baseline with costs x1.5 ({p})"] = \
                row(p, name, "x1.5").sharpe > row(p, BASELINE, "x1.5").sharpe
        out[key] = checks
    return out


def main():
    symbols = sorted(f[:-4] for f in os.listdir(os.path.join(archive.ARCHIVE_DIR, "1d")) if f.endswith(".csv"))
    frames = archive.load_all(symbols, refresh=False, log=lambda *a: None)
    table, curves = run(sv.build_assets(frames))
    table.to_csv(paths.results("exit_results.csv"), index=False, float_format="%.6g")
    pd.set_option("display.width", 200)
    print(table.round(2).to_string(index=False))
    for key, checks in verdict(table).items():
        name = PRIMARY[key]
        print(f"\n{key}: {name}")
        for p in ("B", "A"):
            d = sv.bootstrap_sharpe_diff(curves[(p, name, "base")].pct_change().dropna(),
                                         curves[(p, BASELINE, "base")].pct_change().dropna())
            lo, hi = np.percentile(d, [5, 95])
            print(f"  Period {p}: Sharpe({key}) - Sharpe(baseline) 90% CI [{lo:.2f}, {hi:.2f}], "
                  f"P(diff <= 0) = {(d <= 0).mean():.3f}")
        for label, ok in checks.items():
            print(f"  {'✅' if ok else '❌'} {label}")
        print(f"  {'PASS' if all(checks.values()) else 'FAIL'}")


if __name__ == "__main__":
    main()
