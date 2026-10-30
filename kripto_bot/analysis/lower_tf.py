# kripto_bot/analysis/lower_tf.py
"""v9's lower-timeframe rules E and X vs the baseline and variants H and T
(docs/v9-lower-tf-test.md).

Same point-in-time top-50 universes, equal weights and costs as the exit test. The
baseline, H and T sleeves come from the exit test's own code path (validity check);
E and X run through lower_timeframes.run_backtest, which takes the paper bot's
decisions, on the archive's 4h and 1h candles.

    python -m kripto_bot lower-tf-test --download     # 4h and 1h archive data only
    python -m kripto_bot lower-tf-test                # the test (runs once)
"""
import argparse
import os
from dataclasses import replace
from functools import partial
from multiprocessing import Pool

import numpy as np
import pandas as pd

from kripto_bot import config, paths
from kripto_bot.analysis import exits
from kripto_bot.analysis import survivorship as sv
from kripto_bot.core import archive, strategy
from kripto_bot.core import lower_timeframes as ltf
from kripto_bot.core.indicators import add_indicators

LOWER = ("4h", "1h")
V9 = {"fast": 1, "slow": 50}
BASELINE, H, T = exits.BASELINE, exits.PRIMARY["H"], exits.PRIMARY["T"]
COMPARATOR = {"E": H, "X": T}
STRESSED = [BASELINE, H, T, "E", "X"]          # also run with costs x 1.5
RESULTS = "lower_tf_results.csv"
DAY = pd.Timedelta(days=1)


def variants():
    """{name: (v9 params, trailing ATR multiple, rule E, rule X, lower timeframes)}."""
    band = {"entry_band": exits.BAND}
    return {
        BASELINE: ({}, None, False, False, ()),
        H: (band, None, False, False, ()),
        T: ({}, exits.TRAIL, False, False, ()),
        "E": ({}, None, True, False, LOWER),
        "X": ({}, None, False, True, LOWER),
        "E 4h only": ({}, None, True, False, ("4h",)),
        "E 1h only": ({}, None, True, False, ("1h",)),
        "X 4h only": ({}, None, False, True, ("4h",)),
        "X 1h only": ({}, None, False, True, ("1h",)),
        "E + X": ({}, None, True, True, LOWER),
        f"{H} + E + X": (band, None, True, True, LOWER),
        f"{T} + E + X": ({}, exits.TRAIL, True, True, LOWER),
    }


def runs():
    return [(name, "base") for name in variants()] + [(name, "x1.5") for name in STRESSED]


# ---------------------------------------------------------------- sleeves
def in_position(trades, index):
    """True on days whose close the strategy holds the asset. Like survivorship.in_position,
    but an exit inside a day (rule X) already ends the holding at that day's close; for
    exits at a day's open the two agree."""
    pos = np.zeros(len(index), dtype=bool)
    for e, x in zip(trades["entry_time"], trades["exit_time"]):
        mask = index >= pd.Timestamp(e)
        if pd.notna(x):
            mask &= index + DAY <= pd.Timestamp(x)
        pos |= np.asarray(mask)
    return pd.Series(pos, index=index)


def lower_frames(asset, df, load=archive.load_symbol):
    """The asset's 4h and 1h candles: those of its own days (a reused ticker is split
    into assets, survivorship.build_assets)."""
    symbol = asset.split("#")[0]
    start, end = df["timestamp"].iloc[0], df["timestamp"].iloc[-1] + DAY
    out = {}
    for tf in LOWER:
        c = load(symbol, tf, refresh=False)
        out[tf] = c[(c["timestamp"] >= start) & (c["timestamp"] < end)].reset_index(drop=True)
    return out


def lower_sleeve(df, lower, params, cfg, entry, exit, tfs, signal_cache):
    """(sleeve, exit times of rule X) of one asset over its whole history. Lower-timeframe
    signals use the same v9 parameters as the daily ones, as the paper bot does."""
    fn = partial(strategy.get_strategy("v9"), **{**V9, **params})
    key = tuple(sorted(params.items()))
    if key not in signal_cache:
        signal_cache[key] = {tf: ltf.with_signals(lower[tf].copy(), fn) for tf in LOWER}
    frames = {tf: signal_cache[key][tf] for tf in tfs}
    d = add_indicators(df.copy())
    trades, eq = ltf.run_backtest(d, fn(d), frames, cfg, "1d", entry, exit)
    eq.index = pd.DatetimeIndex(eq.index)
    sleeve = (eq, in_position(trades, eq.index), pd.DatetimeIndex(trades["entry_time"]))
    return sleeve, pd.DatetimeIndex(trades.loc[trades["reason"] == "LTF", "exit_time"])


def _job(job):
    """Every run of one asset: {(variant, costs): (sleeve, rule X exit times)}."""
    asset, df, cfgs, load = job
    lower = lower_frames(asset, df, load) if load else None
    cache, out = {}, {}
    for name, costs in runs():
        params, trail, entry, exit, tfs = variants()[name]
        cfg = replace(cfgs[costs], trail_atr=trail)
        if not tfs:
            out[(name, costs)] = (sv.strategy_sleeve(df, "v9", {**V9, **params}, cfg), pd.DatetimeIndex([]))
        else:
            out[(name, costs)] = lower_sleeve(df, lower, params, cfg, entry, exit, tfs, cache)
    return asset, out


# ---------------------------------------------------------------- portfolio
def configs():
    base = replace(config.engine_config(), atr_multiplier=None, allow_short=False)
    stress = replace(base, fee_rate=base.fee_rate * config.COST_STRESS,
                     slippage_bps=base.slippage_bps * config.COST_STRESS)
    return {"base": base, "x1.5": stress}


def universes(assets, n=50):
    return {p: sv.universe(assets, sv.month_starts(*sv.PERIODS[p]), n) for p in ("B", "A")}


def used_assets(unis):
    return sorted({a for u in unis.values() for ms in u.values() for a in ms})


def count_in(members, times, end):
    """Rule X exits of the month's members inside each month."""
    months = sorted(members)
    total = 0
    for k, m in enumerate(months):
        stop = months[k + 1] if k + 1 < len(months) else pd.Timestamp(end)
        total += sum(int(((times[a] >= m) & (times[a] < stop)).sum()) for a in members[m])
    return total


def run(assets, n=50, workers=None, load=archive.load_symbol, log=print):
    cfgs = configs()
    unis = universes(assets, n)
    used = used_assets(unis)
    log(f"{len(used)} assets in the universes, {len(runs())} runs each")
    sleeves, ltf_exits = {}, {}
    jobs = [(a, assets[a], cfgs, load) for a in used]
    pool = Pool(workers or max(1, (os.cpu_count() or 2) - 1)) if workers != 0 else None   # 0: in this process
    try:
        for k, (asset, out) in enumerate((pool.imap_unordered if pool else map)(_job, jobs), 1):
            for key, (sleeve, x_times) in out.items():
                sleeves[(*key, asset)] = sleeve
                ltf_exits[(*key, asset)] = x_times
            if k % 25 == 0:
                log(f"  {k}/{len(jobs)} assets")
    finally:
        if pool:
            pool.close()
            pool.join()
    rows, curves = [], {}
    for p, members in unis.items():
        for name, costs in runs():
            values, expo, entries = sv.portfolio({a: sleeves[(name, costs, a)] for a in used}, members,
                                                 sv.PERIODS[p][1], sv._fill_cost(cfgs[costs]))
            x = count_in(members, {a: ltf_exits[(name, costs, a)] for a in used}, sv.PERIODS[p][1])
            rows.append({"period": p, "variant": name, "costs": costs, **sv.stats(values),
                         "exposure_pct": expo * 100, "entries": entries, "x_exits": x})
            curves[(p, name, costs)] = values
    return pd.DataFrame(rows), curves


# ---------------------------------------------------------------- verdict
def _row(table, p, name, costs="base"):
    t = table
    return t[(t.period == p) & (t.variant == name) & (t.costs == costs)].iloc[0]


def validity(table, reference):
    """Differences of the baseline, H and T rows from the exit test's results ([] if none)."""
    problems = []
    for name in (BASELINE, H, T):
        for costs in ("base", "x1.5"):
            for p in ("B", "A"):
                ref = reference[(reference.period == p) & (reference.variant == name) & (reference.costs == costs)]
                if ref.empty:
                    continue
                mine, ref = _row(table, p, name, costs), ref.iloc[0]
                for col in ("return_pct", "sharpe", "max_dd_pct", "entries"):
                    if not np.isclose(mine[col], ref[col], rtol=1e-5, atol=1e-6):
                        problems.append(f"{name} {costs} {p} {col}: {mine[col]:.6g} vs {ref[col]:.6g}")
    return problems


def verdict(table):
    """{variant: ({criterion: bool}, preferred over its comparator)}."""
    out = {}
    for key, comp in COMPARATOR.items():
        checks = {}
        for p in ("B", "A"):
            v, b = _row(table, p, key), _row(table, p, BASELINE)
            checks[f"Sharpe above the baseline ({p})"] = v.sharpe > b.sharpe
            checks[f"max drawdown not above the baseline ({p})"] = v.max_dd_pct <= b.max_dd_pct
        for p in ("B", "A"):
            checks[f"Sharpe above the baseline with costs x1.5 ({p})"] = \
                _row(table, p, key, "x1.5").sharpe > _row(table, p, BASELINE, "x1.5").sharpe
        preferred = all(_row(table, p, key).sharpe >= _row(table, p, comp).sharpe for p in ("B", "A"))
        out[key] = (checks, preferred)
    return out


def _ci(curves, p, a, b):
    d = sv.bootstrap_sharpe_diff(curves[(p, a, "base")].pct_change().dropna(),
                                 curves[(p, b, "base")].pct_change().dropna())
    lo, hi = np.percentile(d, [5, 95])
    return f"90% CI [{lo:.2f}, {hi:.2f}], P(diff <= 0) = {(d <= 0).mean():.3f}"


def load_assets():
    symbols = sorted(f[:-4] for f in os.listdir(os.path.join(archive.ARCHIVE_DIR, "1d")) if f.endswith(".csv"))
    return sv.build_assets(archive.load_all(symbols, refresh=False, log=lambda *a: None))


def coverage(assets, unis, load=archive.load_symbol):
    """Share of the member days of each period that have all 24 hourly and 6 four-hour candles."""
    out, counts = {}, {}
    for p, members in unis.items():
        have = total = 0
        months = sorted(members)
        for k, m in enumerate(months):
            stop = months[k + 1] if k + 1 < len(months) else pd.Timestamp(sv.PERIODS[p][1])
            for a in members[m]:
                if a not in counts:                      # candles per day, read once per asset
                    lower = lower_frames(a, assets[a], load)
                    counts[a] = {tf: lower[tf]["timestamp"].dt.floor("D").value_counts() for tf in LOWER}
                days = pd.date_range(m, stop, freq="D", inclusive="left")
                days = days[days <= assets[a]["timestamp"].iloc[-1]]
                full = (counts[a]["1h"].reindex(days).fillna(0) >= 24) & (counts[a]["4h"].reindex(days).fillna(0) >= 6)
                have += int(full.sum())
                total += len(days)
        out[p] = have / total if total else float("nan")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--download", action="store_true", help="download the 4h and 1h archive data only")
    args = ap.parse_args()
    assets = load_assets()
    unis = universes(assets)
    used = used_assets(unis)
    if args.download:
        symbols = sorted({a.split("#")[0] for a in used})
        for tf in LOWER:
            print(f"Downloading {tf} candles of {len(symbols)} symbols...")
            got = archive.load_all(symbols, tf)
            print(f"  {len(got)}/{len(symbols)} symbols have {tf} data")
        for p, share in coverage(assets, unis).items():
            print(f"Period {p}: {share:.2%} of member days have every 4h and 1h candle")
        return

    table, curves = run(assets)
    table.to_csv(paths.results(RESULTS), index=False, float_format="%.6g")
    pd.set_option("display.width", 220)
    print(table.round(2).to_string(index=False))
    problems = validity(table, pd.read_csv(paths.results("exit_results.csv")))
    if problems:
        print("\n❌ VALIDITY CHECK FAILED: the baseline, H or T rows differ from results/exit_results.csv.")
        print("   Don't read the E and X rows before the difference is found:\n   " + "\n   ".join(problems))
        return
    print("\n✅ Validity check: the baseline, H and T rows reproduce results/exit_results.csv")
    for key, (checks, preferred) in verdict(table).items():
        comp = COMPARATOR[key]
        print(f"\n{key}")
        for p in ("B", "A"):
            print(f"  Period {p}: Sharpe({key}) - Sharpe(baseline) {_ci(curves, p, key, BASELINE)}")
            print(f"  Period {p}: Sharpe({key}) - Sharpe({comp}) {_ci(curves, p, key, comp)}")
        for label, ok in checks.items():
            print(f"  {'✅' if ok else '❌'} {label}")
        passed = all(checks.values())
        print(f"  {'PASS' if passed else 'FAIL'}"
              + (f"; {'preferred over' if preferred else 'not preferred over'} {comp}" if passed else ""))


if __name__ == "__main__":
    main()
