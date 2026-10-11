# kripto_bot/analysis/sizing.py
"""Inverse-volatility sizing of v9 vs equal weight (docs/v9-sizing-test.md).

Same point-in-time top-50 universes, v9 settings and costs as the survivorship test;
only the monthly weights differ: equal, or proportional to 1/σ of each asset's daily
returns over the previous LOOKBACK days.

    python -m kripto_bot sizing
"""
import os
from dataclasses import replace
from multiprocessing import Pool

import numpy as np
import pandas as pd

from kripto_bot import config, paths
from kripto_bot.analysis import survivorship as sv
from kripto_bot.core import archive

LOOKBACK = 60                 # primary
SENSITIVITY = [30, 90]        # reported only


def _job(job):
    asset, df, cfg = job
    return asset, sv.strategy_sleeve(df, "v9", {"fast": 1, "slow": 50}, cfg)


def run(assets, n=50, workers=None, log=print):
    cfg = replace(config.engine_config(), atr_multiplier=None, allow_short=False)
    cost = sv._fill_cost(cfg)
    universes = {p: sv.universe(assets, sv.month_starts(*sv.PERIODS[p]), n) for p in ("B", "A")}
    used = sorted({a for u in universes.values() for ms in u.values() for a in ms})
    log(f"{len(used)} assets in the universes")
    with Pool(workers or max(1, (os.cpu_count() or 2) - 1)) as pool:
        sleeves = dict(pool.imap_unordered(_job, [(a, assets[a], cfg) for a in used], chunksize=8))
    rows, curves = [], {}
    for p, members in universes.items():
        end = sv.PERIODS[p][1]
        for name, lookback in [("equal weight", None), (f"inverse vol {LOOKBACK}d", LOOKBACK)] + \
                [(f"inverse vol {k}d", k) for k in SENSITIVITY]:
            w = None if lookback is None else sv.inverse_vol_weights(assets, members, lookback)
            values, expo, entries = sv.portfolio(sleeves, members, end, cost, weights=w)
            turnover = None
            if w is not None:
                norm = {m: pd.Series(x) / sum(x.values()) for m, x in w.items()}
                ms = sorted(norm)
                turnover = float(np.mean([norm[b].sub(norm[a], fill_value=0).abs().sum() / 2
                                          for a, b in zip(ms, ms[1:])]) * 100)
            rows.append({"period": p, "sizing": name, **sv.stats(values), "exposure_pct": expo * 100,
                         "monthly_weight_turnover_pct": turnover})
            curves[(p, name)] = values
    return pd.DataFrame(rows), curves


def main():
    symbols = sorted(f[:-4] for f in os.listdir(os.path.join(archive.ARCHIVE_DIR, "1d")) if f.endswith(".csv"))
    frames = archive.load_all(symbols, refresh=False, log=lambda *a: None)
    table, curves = run(sv.build_assets(frames))
    table.to_csv(paths.results("sizing_results.csv"), index=False, float_format="%.6g")
    pd.set_option("display.width", 200)
    print(table.round(2).to_string(index=False))
    primary = f"inverse vol {LOOKBACK}d"
    ok = []
    for p in ("B", "A"):
        ew = table[(table.period == p) & (table.sizing == "equal weight")].iloc[0]
        iv = table[(table.period == p) & (table.sizing == primary)].iloc[0]
        d = sv.bootstrap_sharpe_diff(curves[(p, primary)].pct_change().dropna(),
                                     curves[(p, "equal weight")].pct_change().dropna())
        lo, hi = np.percentile(d, [5, 95])
        print(f"\nPeriod {p}: Sharpe(IV) - Sharpe(EW) 90% CI [{lo:.2f}, {hi:.2f}], P(diff <= 0) = {(d <= 0).mean():.3f}")
        for label, cond in [(f"Sharpe above equal weight ({p})", iv.sharpe > ew.sharpe),
                            (f"max drawdown not above equal weight ({p})", iv.max_dd_pct <= ew.max_dd_pct)]:
            print(f"  {'✅' if cond else '❌'} {label}")
            ok.append(cond)
    print("PASS" if all(ok) else "FAIL")


if __name__ == "__main__":
    main()
