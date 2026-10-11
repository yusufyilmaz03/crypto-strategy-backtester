# kripto_bot/analysis/survivorship.py
"""Survivorship-free test of v9 on a point-in-time universe (docs/v9-survivorship-test.md).

Every USDT pair in the Binance archive takes part, including delisted ones. At the
start of each month the universe is the top N assets by USDT volume over the
previous 30 days (only assets with at least 60 daily candles); the portfolio splits
its value equally across them and each slot follows its asset's strategy equity for
the month. An asset that joins while the strategy holds it costs one fill, one that
leaves (or is delisted) while held costs one fill. The benchmark is buy-and-hold of
the same universe under the same rules.

    python -m kripto_bot survivorship
"""
import argparse
import os
from dataclasses import replace
from multiprocessing import Pool

import numpy as np
import pandas as pd

from kripto_bot import config, paths
from kripto_bot.core import archive, sizing, strategy
from kripto_bot.core.engine import run_backtest
from kripto_bot.core.indicators import add_indicators
from kripto_bot.core.metrics import max_drawdown_pct

QUOTE = "USDT"
# Stablecoins, fiat and gold tokens: not crypto risk, fixed before the test ran.
STABLE_BASES = {
    "USDC", "BUSD", "TUSD", "USDP", "PAX", "DAI", "FDUSD", "USDS", "USDSB", "SUSD", "UST", "USTC",
    "EUR", "GBP", "AUD", "TRY", "BRL", "RUB", "BIDR", "IDRT", "NGN", "UAH", "ZAR", "BKRW", "BVND",
    "PAXG", "AEUR", "EURI", "XUSD", "USD1", "BFUSD", "PYUSD", "USDE", "RLUSD", "USDF", "XAUT", "U", "KGST",
}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")
LEVERAGED_TOKENS = {"BULL", "BEAR"}                 # 3x BTC tokens of 2020
# Tokenized stocks and ETFs (AAPLB, NVDAB, SPYB, ...) Binance listed from June 2026 on.
STOCK_TOKENS_FROM = pd.Timestamp("2026-06-01")
MIN_HISTORY = 60
VOLUME_DAYS = 30
MAX_GAP_DAYS = 5
PERIODS = {"B": ("2018-01-01", "2022-10-01"),     # never used by this project: the primary test
           "A": ("2022-10-01", "2026-10-01")}     # Phase 2b development + spent 1d holdout
NEIGHBORS = [20, 30, 40, 50, 70, 100, 150, 200]


# ---------------------------------------------------------------- universe
def excluded(base, all_bases, first=None):
    if base in STABLE_BASES or base in LEVERAGED_TOKENS:
        return True
    if first is not None and base.endswith("B") and first >= STOCK_TOKENS_FROM:
        return True
    return any(base.endswith(s) and len(base) > len(s) and base[:-len(s)] in all_bases
               for s in LEVERAGED_SUFFIXES)


def build_assets(frames):
    """{asset id: daily frame}. A reused ticker's later listings get ids like LUNAUSDT#2."""
    bases = {s[:-len(QUOTE)] for s in frames}
    out = {}
    for sym, df in sorted(frames.items()):
        if df.empty or excluded(sym[:-len(QUOTE)], bases, df["timestamp"].iloc[0]):
            continue
        for k, piece in enumerate(archive.split_listings(df, MAX_GAP_DAYS)):
            out[sym if k == 0 else f"{sym}#{k + 1}"] = piece
    return out


def month_starts(start, end):
    return list(pd.date_range(start, end, freq="MS", inclusive="left"))


def universe(assets, months, n, allowed=None, min_history=MIN_HISTORY, volume_days=VOLUME_DAYS):
    """{month start: [asset ids]}: the n most traded eligible assets, using only data
    up to the last day of the previous month."""
    vols = {a: df.set_index("timestamp")["quote_volume"] for a, df in assets.items()
            if allowed is None or allowed(a)}
    out = {}
    for m in months:
        prev = m - pd.Timedelta(days=1)
        ranked = []
        for a, qv in vols.items():
            if qv.index[0] > prev or qv.index[-1] < prev:
                continue
            hist = qv.loc[:prev]
            if len(hist) < min_history or hist.index[-1] != prev:
                continue
            ranked.append((hist.iloc[-volume_days:].sum(), a))
        ranked.sort(reverse=True)
        out[m] = [a for _, a in ranked[:n]]
    return out


# ---------------------------------------------------------------- sleeves
def in_position(trades, index):
    """True on days whose close the strategy holds the asset."""
    pos = np.zeros(len(index), dtype=bool)
    for e, x in zip(trades["entry_time"], trades["exit_time"]):
        mask = index >= pd.Timestamp(e)
        if pd.notna(x):
            mask &= index < pd.Timestamp(x)
        pos |= np.asarray(mask)
    return pd.Series(pos, index=index)


def strategy_sleeve(df, key, params, cfg):
    """(equity Series, in-position Series, entry times) of one asset traded on its whole history."""
    d = add_indicators(df.copy())
    signals = strategy.get_strategy(key)(d, **params)
    trades, eq = run_backtest(d, signals, cfg)
    eq.index = pd.DatetimeIndex(eq.index)
    return eq, in_position(trades, eq.index), pd.DatetimeIndex(trades["entry_time"])


def hold_sleeve(df):
    s = df.set_index("timestamp")["close"].astype(float)
    return s, pd.Series(True, index=s.index), pd.DatetimeIndex([])


# ---------------------------------------------------------------- portfolio
def portfolio(sleeves, members, end, cost, weights=None):
    """Daily portfolio value (starting at 1 on the day before the first month) and
    daily exposure. sleeves: {asset: (value, in_position, entries)}; members:
    {month start: [assets]}; cost: one fill (fee + slippage) as a fraction; weights:
    {month start: {asset: weight}} (normalized per month), default equal weight."""
    months = sorted(members)
    end = pd.Timestamp(end)
    value = 1.0
    values = [pd.Series([1.0], index=[months[0] - pd.Timedelta(days=1)])]
    exposure, entries = [], 0
    for k, m in enumerate(months):
        stop = months[k + 1] if k + 1 < len(months) else end
        days = pd.date_range(m, stop, freq="D", inclusive="left")
        prev = m - pd.Timedelta(days=1)
        before = set(members[months[k - 1]]) if k else set()
        after = set(members[months[k + 1]]) if k + 1 < len(months) else None   # None: period ends
        paths, held = [], []
        for a in members[m]:
            v, pos, ent = sleeves[a]
            path = v.reindex(days, method="ffill") / float(v.loc[prev])
            pos_days = pos.reindex(days, method="ffill").fillna(False).astype(bool)
            last = min(v.index[-1], days[-1])
            if pos.loc[prev] and a not in before:
                path = path * (1 - cost)                          # bought when it joined
            delisted = v.index[-1] < days[-1]
            if delisted:
                pos_days[days > last] = False
            leaving = delisted or (after is not None and a not in after)
            if leaving and bool(pos.reindex([last], method="ffill").iloc[0]):
                path[days >= last] *= (1 - cost)                  # sold when it left
            paths.append(path)
            held.append(pos_days)
            entries += int(((ent >= m) & (ent < stop)).sum())
        if paths:
            w = np.full(len(paths), 1.0) if weights is None else np.array([weights[m][a] for a in members[m]])
            w = w / w.sum()
            month = pd.concat(paths, axis=1).mul(w, axis=1).sum(axis=1)
            exposure.append(pd.concat(held, axis=1).astype(float).mul(w, axis=1).sum(axis=1))
        else:
            month = pd.Series(1.0, index=days)
            exposure.append(pd.Series(0.0, index=days))
        values.append(month * value)
        value = float(month.iloc[-1] * value)
    return pd.concat(values), float(pd.concat(exposure).mean()), entries


def inverse_vol_weights(assets, members, lookback=60):
    """{month: {asset: 1/σ}} with σ the standard deviation of the asset's daily close-to-close
    returns over the lookback days ending the day before the month (no later data)."""
    closes = {a: df.set_index("timestamp")["close"] for a, df in assets.items()}
    out = {}
    for m, names in members.items():
        prev = m - pd.Timedelta(days=1)
        out[m] = sizing.inverse_vol_weights({a: closes[a].loc[:prev] for a in names}, lookback) if names else {}
    return out


def stats(values):
    r = values.pct_change().dropna()
    years = len(r) / 365
    sharpe = float(r.mean() / r.std(ddof=1) * np.sqrt(365)) if r.std(ddof=1) > 0 else float("nan")
    total = float(values.iloc[-1] / values.iloc[0] - 1)
    return {"return_pct": total * 100, "cagr_pct": ((1 + total) ** (1 / years) - 1) * 100,
            "sharpe": sharpe, "max_dd_pct": max_drawdown_pct(values), "days": len(r)}


def bootstrap_sharpe_diff(r1, r2, n=5000, block=20, seed=0):
    """Stationary bootstrap (Politis & Romano) of paired daily returns: Sharpe(r1) - Sharpe(r2)."""
    r1, r2 = np.asarray(r1), np.asarray(r2)
    T = len(r1)
    rng = np.random.default_rng(seed)
    t = np.arange(T)
    out = np.empty(n)
    for b in range(n):
        starts = rng.random(T) < 1 / block
        starts[0] = True
        last = np.maximum.accumulate(np.where(starts, t, 0))
        idx = (rng.integers(T, size=T)[last] + t - last) % T
        a, c = r1[idx], r2[idx]
        out[b] = (a.mean() / a.std(ddof=1) - c.mean() / c.std(ddof=1)) * np.sqrt(365)
    return out


# ---------------------------------------------------------------- runner
def _fill_cost(cfg):
    return cfg.fee_rate + cfg.slippage_bps / 10_000


def _sleeve_job(job):
    asset, df, slow, cfg = job
    return asset, slow, cfg.fee_rate, strategy_sleeve(df, "v9", {"fast": 1, "slow": slow}, cfg)


def run(assets, n=50, log=print, workers=None):
    base = replace(config.engine_config(), atr_multiplier=None, allow_short=False)
    stress = replace(base, fee_rate=base.fee_rate * config.COST_STRESS,
                     slippage_bps=base.slippage_bps * config.COST_STRESS)
    survivors = {s.replace("/", "") for s in config.SYMBOLS}
    last_piece = {}
    for a in assets:
        last_piece[a.split("#")[0]] = a                    # sorted ids: the last listing wins

    universes = {}
    for p, (start, end) in PERIODS.items():
        months = month_starts(start, end)
        universes[(p, f"top{n}")] = universe(assets, months, n)
        universes[(p, "top20")] = universe(assets, months, 20)
    months_a = month_starts(*PERIODS["A"])
    universes[("A", "survivors")] = universe(assets, months_a, len(survivors),
                                             allowed=lambda a: a.split("#")[0] in survivors
                                             and last_piece[a.split("#")[0]] == a)
    used = sorted({a for u in universes.values() for ms in u.values() for a in ms})
    log(f"{len(assets)} assets, {len(used)} ever in a universe")

    jobs = [(a, assets[a], 50, base) for a in used] + [(a, assets[a], 50, stress) for a in used]
    used_b = sorted({a for ms in universes[("B", f"top{n}")].values() for a in ms})
    jobs += [(a, assets[a], s, base) for s in NEIGHBORS if s != 50 for a in used_b]
    sleeves = {}
    with Pool(workers or max(1, (os.cpu_count() or 2) - 1)) as pool:
        for k, (a, slow, fee, sl) in enumerate(pool.imap_unordered(_sleeve_job, jobs, chunksize=8), 1):
            sleeves[(slow, "stress" if fee > base.fee_rate else "base", a)] = sl
            if k % 500 == 0:
                log(f"  {k}/{len(jobs)} backtests")
    hold = {a: hold_sleeve(assets[a]) for a in used}

    rows, curves = [], {}

    def add(period, uni, name, costs, sl, cost):
        members = universes[(period, uni)]
        values, expo, entries = portfolio(sl, members, PERIODS[period][1], cost)
        rows.append({"period": period, "universe": uni, "strategy": name, "costs": costs, **stats(values),
                     "exposure_pct": expo * 100, "entries": entries,
                     "avg_members": float(np.mean([len(v) for v in members.values()]))})
        curves[(period, uni, name, costs)] = values

    for (period, uni) in universes:
        pick = lambda slow, c: {a: sleeves[(slow, c, a)] for a in hold}  # noqa: E731
        add(period, uni, "v9 slow=50", "base", pick(50, "base"), _fill_cost(base))
        add(period, uni, "buy-and-hold", "base", hold, _fill_cost(base))
        add(period, uni, "v9 slow=50", "x1.5", pick(50, "stress"), _fill_cost(stress))
        add(period, uni, "buy-and-hold", "x1.5", hold, _fill_cost(stress))
    for slow in NEIGHBORS:
        if slow != 50:
            add("B", f"top{n}", f"v9 slow={slow}", "base",
                {a: sleeves[(slow, "base", a)] for a in used_b}, _fill_cost(base))
    return pd.DataFrame(rows), curves, universes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-download", action="store_true", help="use the cached archive only")
    a = ap.parse_args()
    symbols = archive.list_symbols(QUOTE) if not a.no_download else sorted(
        f[:-4] for f in os.listdir(os.path.join(archive.ARCHIVE_DIR, "1d")))
    frames = archive.load_all(symbols, refresh=not a.no_download)
    assets = build_assets(frames)
    table, curves, universes = run(assets)
    table.to_csv(paths.results("survivorship_results.csv"), index=False, float_format="%.6g")
    pd.DataFrame([{"period": p, "universe": u, "month": m.date(), "members": " ".join(ms)}
                  for (p, u), uni in universes.items() for m, ms in uni.items()]).to_csv(
        paths.results("survivorship_universes.csv"), index=False)

    b, h = curves[("B", "top50", "v9 slow=50", "base")], curves[("B", "top50", "buy-and-hold", "base")]
    diffs = bootstrap_sharpe_diff(b.pct_change().dropna(), h.pct_change().dropna())
    pd.set_option("display.width", 200)
    print(table.round(2).to_string(index=False))
    lo, hi = np.percentile(diffs, [5, 95])
    print(f"\nPeriod B top50, Sharpe(v9) - Sharpe(B&H): 90% bootstrap CI [{lo:.2f}, {hi:.2f}], "
          f"P(diff <= 0) = {(diffs <= 0).mean():.3f}")

    def row(uni, name, costs, period="B"):
        t = table
        return t[(t.period == period) & (t.universe == uni) & (t.strategy == name) & (t.costs == costs)].iloc[0]
    v, bh, vs, bhs = (row("top50", "v9 slow=50", "base"), row("top50", "buy-and-hold", "base"),
                      row("top50", "v9 slow=50", "x1.5"), row("top50", "buy-and-hold", "x1.5"))
    checks = {"Sharpe above buy-and-hold": v.sharpe > bh.sharpe,
              "max drawdown below buy-and-hold": v.max_dd_pct < bh.max_dd_pct,
              "return above zero": v.return_pct > 0,
              "Sharpe above buy-and-hold with costs x1.5": vs.sharpe > bhs.sharpe}
    for k, ok in checks.items():
        print(f"  {'✅' if ok else '❌'} {k}")
    print("PASS" if all(checks.values()) else "FAIL")
    nb = table[(table.period == "B") & (table.universe == "top50") & table.strategy.str.startswith("v9")
               & (table.costs == "base")]
    beat = ((nb.sharpe > bh.sharpe) & (nb.max_dd_pct < bh.max_dd_pct)).sum()
    print(f"Neighborhood: {beat}/{len(nb)} SMA lengths beat buy-and-hold on Sharpe and drawdown "
          f"({'SMA50 is not isolated' if beat >= 6 else 'SMA50 looks isolated'})")


if __name__ == "__main__":
    main()
