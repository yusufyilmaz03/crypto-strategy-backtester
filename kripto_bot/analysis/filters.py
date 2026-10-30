# kripto_bot/analysis/filters.py
"""v9's reliability filters judged by their trades (docs/v9-filters-test.md).

The trend filter, the SMA mode of rules E and X and the combinations E + X and H + E + X
against the baseline, on the universes, costs and 4h/1h archive data of the
lower-timeframe test. Every variant runs through lower_timeframes.run_backtest, which
takes the paper bot's decisions (and is the engine's backtest when no rule is on).

    python -m kripto_bot filters-test      # runs once; needs lower-tf-test --download first
"""
import os
from dataclasses import dataclass, replace
from functools import partial
from multiprocessing import Pool

import numpy as np
import pandas as pd

from kripto_bot import paths
from kripto_bot.analysis import exits, lower_tf
from kripto_bot.analysis import survivorship as sv
from kripto_bot.core import archive, strategy
from kripto_bot.core import lower_timeframes as ltf
from kripto_bot.core.indicators import add_indicators

RESULTS = "filters_results.csv"
BASELINE, H, T = lower_tf.BASELINE, lower_tf.H, lower_tf.T
PRIMARY = ["S200", "E200", "X200", "E+X", "H+E+X"]
STRESSED = [BASELINE, *PRIMARY]                         # also run with costs x 1.5
REFERENCE = [BASELINE, H, T, "E", "X"]                  # rows of results/lower_tf_results.csv
TRADE_COLS = ["entry_time", "exit_time", "return_pct", "reason"]


@dataclass(frozen=True)
class Variant:
    params: tuple = ()          # v9 parameters on top of fast=1, slow=50
    trail: float | None = None
    entry: bool = False         # rule E
    exit: bool = False          # rule X
    lower_sma: int | None = None  # SMA mode of E and X (None: v9's signal)
    trend: int | None = None    # trend filter length


def variants():
    band = (("entry_band", exits.BAND),)
    return {
        BASELINE: Variant(),
        H: Variant(params=band),
        T: Variant(trail=exits.TRAIL),
        "E": Variant(entry=True),
        "X": Variant(exit=True),
        "S200": Variant(trend=200),
        "E200": Variant(entry=True, lower_sma=200),
        "X200": Variant(exit=True, lower_sma=200),
        "E+X": Variant(entry=True, exit=True),
        "H+E+X": Variant(params=band, entry=True, exit=True),
        "S100": Variant(trend=100),
        "S150": Variant(trend=150),
        "S250": Variant(trend=250),
        "E100": Variant(entry=True, lower_sma=100),
        "X100": Variant(exit=True, lower_sma=100),
        "S200 + X": Variant(trend=200, exit=True),
        "E50": Variant(entry=True, lower_sma=50),       # validity: must equal E
        "X50": Variant(exit=True, lower_sma=50),        # validity: must equal X
    }


def runs():
    return [(name, "base") for name in variants()] + [(name, "x1.5") for name in STRESSED]


# ---------------------------------------------------------------- sleeves
def sleeve(df, lower, v, cfg, cache):
    """((equity, in position, entries), trades) of one asset over its whole history."""
    params = {**lower_tf.V9, **dict(v.params)}
    fn = partial(strategy.get_strategy("v9"), **params)
    frames = None
    if v.entry or v.exit:
        key = (v.params, v.lower_sma)
        if key not in cache:
            lower_fn = ltf.sma_signal(v.lower_sma) if v.lower_sma else fn
            cache[key] = {tf: ltf.with_signals(lower[tf].copy(), lower_fn) for tf in lower_tf.LOWER}
        frames = cache[key]
    d = add_indicators(df.copy())
    trend = ltf.trend_filter(d, v.trend) if v.trend else None
    trades, eq = ltf.run_backtest(d, fn(d), frames, replace(cfg, trail_atr=v.trail), "1d", v.entry, v.exit,
                                  trend=trend)
    eq.index = pd.DatetimeIndex(eq.index)
    return (eq, lower_tf.in_position(trades, eq.index), pd.DatetimeIndex(trades["entry_time"])), trades[TRADE_COLS]


def _job(job):
    asset, df, cfgs, load = job
    lower = lower_tf.lower_frames(asset, df, load) if load else None
    cache, out = {}, {}
    for name, costs in runs():
        out[(name, costs)] = sleeve(df, lower, variants()[name], cfgs[costs], cache)
    return asset, out


# ---------------------------------------------------------------- trades
def member_trades(members, trades, end):
    """The closed trades entered while their asset was a member, exited before `end`
    (trades closed at the end of the data left out). trades: {asset: frame}."""
    months = sorted(members)
    picked = []
    for k, m in enumerate(months):
        stop = months[k + 1] if k + 1 < len(months) else pd.Timestamp(end)
        for a in members[m]:
            t = trades[a]
            e = pd.to_datetime(t["entry_time"])
            sel = (e >= m) & (e < stop) & (t["reason"] != "END") & (pd.to_datetime(t["exit_time"]) < pd.Timestamp(end))
            picked.append(t[sel])
    return pd.concat(picked) if picked else pd.DataFrame(columns=TRADE_COLS)


def trade_stats(trades):
    r = trades["return_pct"].astype(float)
    wins, losses = r[r > 0], r[r <= 0]
    loss_sum = -losses.sum()
    return {"trades": int(len(r)),
            "win_rate_pct": float((r > 0).mean() * 100) if len(r) else float("nan"),
            "profit_factor": float(wins.sum() / loss_sum) if loss_sum > 0 else float("inf"),
            "expectancy_pct": float(r.mean()) if len(r) else float("nan"),
            "avg_win_pct": float(wins.mean()) if len(wins) else float("nan"),
            "avg_loss_pct": float(losses.mean()) if len(losses) else float("nan")}


# ---------------------------------------------------------------- run
def run(assets, n=50, workers=None, load=archive.load_symbol, log=print):
    cfgs = lower_tf.configs()
    unis = lower_tf.universes(assets, n)
    used = lower_tf.used_assets(unis)
    log(f"{len(used)} assets in the universes, {len(runs())} runs each")
    sleeves, trades = {}, {}
    jobs = [(a, assets[a], cfgs, load) for a in used]
    pool = Pool(workers or max(1, (os.cpu_count() or 2) - 1)) if workers != 0 else None   # 0: in this process
    try:
        for k, (asset, out) in enumerate((pool.imap_unordered if pool else map)(_job, jobs), 1):
            for key, (sl, tr) in out.items():
                sleeves[(*key, asset)], trades[(*key, asset)] = sl, tr
            if k % 25 == 0:
                log(f"  {k}/{len(jobs)} assets")
    finally:
        if pool:
            pool.close()
            pool.join()
    rows, curves = [], {}
    for p, members in unis.items():
        end = sv.PERIODS[p][1]
        for name, costs in runs():
            values, expo, entries = sv.portfolio({a: sleeves[(name, costs, a)] for a in used}, members, end,
                                                 sv._fill_cost(cfgs[costs]))
            mine = {a: trades[(name, costs, a)] for a in used}
            x_times = {a: pd.DatetimeIndex(t.loc[t["reason"] == "LTF", "exit_time"]) for a, t in mine.items()}
            rows.append({"period": p, "variant": name, "costs": costs, **sv.stats(values),
                         "exposure_pct": expo * 100, "entries": entries,
                         "x_exits": lower_tf.count_in(members, x_times, end),
                         **trade_stats(member_trades(members, mine, end))})
            curves[(p, name, costs)] = values
    return pd.DataFrame(rows), curves


# ---------------------------------------------------------------- checks and verdict
def _row(table, p, name, costs="base"):
    return lower_tf._row(table, p, name, costs)


def validity(table, reference):
    """Problems with the pre-registered validity checks ([] if none)."""
    problems = []
    for name in REFERENCE:
        for costs in ("base", "x1.5"):
            for p in ("B", "A"):
                ref = reference[(reference.period == p) & (reference.variant == name) & (reference.costs == costs)]
                mine = table[(table.period == p) & (table.variant == name) & (table.costs == costs)]
                if ref.empty or mine.empty:
                    continue
                for col in ("return_pct", "sharpe", "max_dd_pct", "entries"):
                    a, b = mine.iloc[0][col], ref.iloc[0][col]
                    if not np.isclose(a, b, rtol=1e-5, atol=1e-6):
                        problems.append(f"{name} {costs} {p} {col}: {a:.6g} vs {b:.6g} in lower_tf_results.csv")
    cols = [c for c in table.columns if c not in ("period", "variant", "costs")]
    for sma, rule in (("E50", "E"), ("X50", "X")):
        for p in ("B", "A"):
            a, b = _row(table, p, sma), _row(table, p, rule)
            for col in cols:
                if not np.isclose(a[col], b[col], rtol=1e-9, atol=1e-9, equal_nan=True):
                    problems.append(f"{sma} vs {rule} {p} {col}: {a[col]:.6g} vs {b[col]:.6g}")
    return problems


def verdict(table):
    """{primary variant: {criterion: bool}}."""
    out = {}
    for name in PRIMARY:
        checks = {}
        for costs, suffix in (("base", ""), ("x1.5", " with costs x1.5")):
            for p in ("B", "A"):
                v, b = _row(table, p, name, costs), _row(table, p, BASELINE, costs)
                checks[f"win rate higher ({p}){suffix}"] = v.win_rate_pct > b.win_rate_pct
                checks[f"profit factor not lower ({p}){suffix}"] = v.profit_factor >= b.profit_factor
                checks[f"expectancy not lower ({p}){suffix}"] = v.expectancy_pct >= b.expectancy_pct
                if costs == "base":
                    checks[f"max drawdown not higher ({p})"] = v.max_dd_pct <= b.max_dd_pct
        out[name] = checks
    return out


def main():
    assets = lower_tf.load_assets()
    table, curves = run(assets)
    table.to_csv(paths.results(RESULTS), index=False, float_format="%.6g")
    pd.set_option("display.width", 260)
    show = ["period", "variant", "costs", "return_pct", "sharpe", "max_dd_pct", "exposure_pct", "trades",
            "win_rate_pct", "profit_factor", "expectancy_pct", "avg_win_pct", "avg_loss_pct", "x_exits"]
    print(table[show].round(2).to_string(index=False))
    problems = validity(table, pd.read_csv(paths.results(lower_tf.RESULTS)))
    if problems:
        print("\n❌ VALIDITY CHECK FAILED. Don't read the results before the difference is found:\n   "
              + "\n   ".join(problems))
        return
    print("\n✅ Validity checks: the reference rows reproduce results/lower_tf_results.csv; E50 = E and X50 = X")
    for name, checks in verdict(table).items():
        print(f"\n{name}")
        for p in ("B", "A"):
            print(f"  Period {p}: Sharpe({name}) - Sharpe(baseline) {lower_tf._ci(curves, p, name, BASELINE)}")
        for label, ok in checks.items():
            print(f"  {'✅' if ok else '❌'} {label}")
        print(f"  {'PASS' if all(checks.values()) else 'FAIL'}")


if __name__ == "__main__":
    main()
