# research.py
"""Strategy research with walk-forward optimization and a held-out final test.

Development run (default):
  For every research timeframe the last HOLDOUT_FRACTION of the history is set
  aside. On the rest, each single-asset strategy is walk-forward optimized per
  symbol, and the cross-sectional rotation (R1) on the whole universe. Each
  strategy/timeframe candidate becomes an equal-weight portfolio of out-of-sample
  curves, compared with equal-weight buy-and-hold and checked with a deflated
  Sharpe ratio that counts every candidate tested so far. Candidates that meet the
  pre-registered criteria are marked as finalists.

Holdout run (--holdout):
  Finalists only: parameters are chosen on the last training window before the
  holdout and run once on the holdout.

Outputs: research_results.csv, research_folds.csv, research_summary.csv,
research_meta.json and, for the holdout run, research_holdout.csv.
"""
import argparse
import json
import math
import os
import sys
from multiprocessing import Pool

import pandas as pd

import config
from data import load_ohlcv, now_ms
from indicators import add_indicators
from metrics import deflated_sharpe, max_drawdown_pct
from optimize import holdout_test, walk_forward_optimize
from rotation import build_panel, holdout_rotation, walk_forward_rotation

RESULTS_FILE = "research_results.csv"
FOLDS_FILE = "research_folds.csv"
SUMMARY_FILE = "research_summary.csv"
META_FILE = "research_meta.json"
HOLDOUT_FILE = "research_holdout.csv"

MIN_TOTAL_TRADES = 30
MIN_DSR = 0.95
ROTATION_KEY = "R1"


# ---------- data ----------
def prefetch(symbols, timeframes):
    """Download/refresh the cache sequentially so workers never hit the network."""
    for tf, days in timeframes.items():
        for symbol in symbols:
            try:
                print(f"📥 {symbol} {tf}: {len(load_ohlcv(symbol, tf, days))} candles")
            except Exception as e:
                print(f"[ERROR] download {symbol} {tf}: {e}")


def holdout_cutoffs(timeframes, now=None):
    now = pd.Timestamp(now_ms() if now is None else now, unit="ms")
    return {tf: now - pd.Timedelta(days=days * config.HOLDOUT_FRACTION) for tf, days in timeframes.items()}


def _load(symbol, tf, days):
    return load_ohlcv(symbol, tf, days, refresh=False)


# ---------- development ----------
def _run_task(task):
    symbol, tf, days, strategies, cutoff = task
    df = _load(symbol, tf, days)
    dev = df[df["timestamp"] < cutoff].reset_index(drop=True)
    if len(dev) < config.RESEARCH_MIN_CANDLES:
        return symbol, tf, [], f"only {len(dev)} development candles"
    dev = add_indicators(dev)
    out = []
    for key in strategies:
        res = walk_forward_optimize(
            dev, key, tf, config.engine_config(), n_folds=config.WFO_FOLDS,
            min_train_fraction=config.WFO_MIN_TRAIN_FRACTION, min_trades=config.WFO_MIN_TRADES,
            stops=config.RESEARCH_STOPS, cost_multiplier=config.COST_STRESS)
        closes = dev.set_index("timestamp")["close"].loc[res["oos_equity"].index[0]:]
        out.append((key, res, closes))
    return symbol, tf, out, None


def _daily_returns(equity):
    return equity.resample("1D").last().dropna().pct_change().dropna()


def _portfolio(curves):
    """Equal-weight portfolio of per-symbol curves (each rebased), as an equity curve."""
    rets = pd.concat({k: c.pct_change() for k, c in curves.items()}, axis=1).sort_index()
    return (1 + rets.mean(axis=1, skipna=True).fillna(0)).cumprod()


def _sharpe_daily(daily):
    if len(daily) < 3 or not daily.std(ddof=1) > 0:
        return float("nan")
    return daily.mean() / daily.std(ddof=1)


def _candidate_row(key, tf, n_symbols, eq, eq_cost, bh, trades, extra):
    d, d_bh = _daily_returns(eq), _daily_returns(bh)
    row = {
        "Strategy": key, "Timeframe": tf, "Symbols": n_symbols,
        "OOSStart": eq.index[0], "OOSEnd": eq.index[-1],
        "Return%": (eq.iloc[-1] / eq.iloc[0] - 1) * 100 if eq.iloc[0] else float("nan"),
        "BuyHold%": (bh.iloc[-1] / bh.iloc[0] - 1) * 100,
        "StressReturn%": (eq_cost.iloc[-1] / eq_cost.iloc[0] - 1) * 100,
        "Sharpe": _sharpe_daily(d) * math.sqrt(365),
        "BuyHoldSharpe": _sharpe_daily(d_bh) * math.sqrt(365),
        "MaxDD%": max_drawdown_pct(eq), "BuyHoldMaxDD%": max_drawdown_pct(bh),
        "Trades": int(trades), **extra,
    }
    return row, d


def summarize(per_symbol, rotations=None, prior_trials=0):
    """One row per (strategy, timeframe) candidate, with DSR and the pass flag."""
    rows, daily = [], {}
    for (key, tf), items in per_symbol.items():
        eq = _portfolio({s: r["oos_equity"] for s, r, _ in items})
        eq_cost = _portfolio({s: r["high_cost_equity"] for s, r, _ in items})
        bh = _portfolio({s: c for s, _, c in items})
        sym_returns = pd.Series([r["oos"]["return_pct"] for _, r, _ in items])
        row, d = _candidate_row(key, tf, len(items), eq, eq_cost, bh,
                                sum(r["oos"]["trades"] for _, r, _ in items), {
            "MedianSymbolReturn%": sym_returns.median(),
            "SymbolsPositive%": (sym_returns > 0).mean() * 100,
            "MedianDefaultReturn%": pd.Series([r["default_return_pct"] for _, r, _ in items]).median(),
        })
        rows.append(row)
        daily[(key, tf)] = d
    for tf, res in (rotations or {}).items():
        bh = _portfolio({s: res["bh_closes"][s].dropna() for s in res["bh_closes"].columns
                         if res["bh_closes"][s].notna().sum() > 1})
        row, d = _candidate_row(ROTATION_KEY, tf, res["bh_closes"].shape[1], res["oos_equity"],
                                res["high_cost_equity"], bh, res["trades"], {})
        rows.append(row)
        daily[(ROTATION_KEY, tf)] = d

    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    # Deflated Sharpe: the best of all candidates tested so far vs. what luck alone would give.
    srs = pd.Series({k: _sharpe_daily(d) for k, d in daily.items()}).dropna()
    sr_std = srs.std(ddof=1) if len(srs) > 1 else 0.0
    n_trials = len(summary) + prior_trials
    summary["Trials"] = n_trials
    summary["DSR"] = [deflated_sharpe(daily[(r.Strategy, r.Timeframe)], n_trials, sr_std)
                      for r in summary.itertuples()]
    summary["Pass"] = ((summary["Return%"] > 0) & (summary["StressReturn%"] > 0)
                       & (summary["Sharpe"] > summary["BuyHoldSharpe"])
                       & (summary["Trades"] >= MIN_TOTAL_TRADES) & (summary["DSR"] >= MIN_DSR))
    return summary.sort_values("Sharpe", ascending=False).reset_index(drop=True)


def _dev_panel(symbols, tf, days, cutoff):
    frames = {}
    for s in symbols:
        df = _load(s, tf, days)
        df = df[df["timestamp"] < cutoff]
        if len(df):
            frames[s] = df
    return build_panel(frames)


def run(symbols=None, timeframes=None, strategies=None, workers=None, fetch=True):
    symbols = symbols or config.SYMBOLS
    timeframes = timeframes or config.RESEARCH_TIMEFRAMES
    strategies = strategies if strategies is not None else config.RESEARCH_STRATEGIES
    if fetch:
        prefetch(symbols, timeframes)
    cutoffs = holdout_cutoffs(timeframes)
    with open(META_FILE, "w") as f:
        json.dump({"timeframes": timeframes, "holdout_start": {tf: str(c) for tf, c in cutoffs.items()},
                   "symbols": symbols, "strategies": strategies}, f, indent=2)
    for tf, c in cutoffs.items():
        print(f"🔒 {tf}: holdout from {c} is not used in development")

    tasks = [(s, tf, days, strategies, cutoffs[tf]) for tf, days in timeframes.items() for s in symbols]
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    result_rows, fold_rows, per_symbol = [], [], {}
    if strategies:
        with Pool(workers) as pool:
            for i, (symbol, tf, out, skipped) in enumerate(pool.imap_unordered(_run_task, tasks), 1):
                if skipped:
                    print(f"[{i}/{len(tasks)}] ⏭️  {symbol} {tf}: skipped ({skipped})")
                    continue
                print(f"[{i}/{len(tasks)}] ✅ {symbol} {tf}")
                for key, res, closes in out:
                    per_symbol.setdefault((key, tf), []).append((symbol, res, closes))
                    o = res["oos"]
                    result_rows.append({
                        "Symbol": symbol, "Timeframe": tf, "Strategy": key,
                        "Return%": o["return_pct"], "BuyHold%": o["buy_hold_pct"],
                        "StressReturn%": res["high_cost_return_pct"], "DefaultReturn%": res["default_return_pct"],
                        "Trades": o["trades"], "WinRate%": o["win_rate_pct"], "ProfitFactor": o["profit_factor"],
                        "MaxDD%": o["max_drawdown_pct"], "Sharpe": o["sharpe"], "Exposure%": o["exposure_pct"],
                        "TrainPositiveShare%": res["train_positive_share"] * 100,
                    })
                    for k, f in enumerate(res["folds"], 1):
                        fold_rows.append({"Symbol": symbol, "Timeframe": tf, "Strategy": key, "Fold": k,
                                          "TestStart": f["test_start"], "TestEnd": f["test_end"],
                                          "Params": f["params"], "TrainSharpe": f["train_sharpe"],
                                          "TestTrades": f["test_trades"], "TestReturn%": f["test_return_pct"]})

    rotations = {}
    for tf in [t for t in config.ROTATION_TIMEFRAMES if t in timeframes]:
        print(f"🔄 Rotation {ROTATION_KEY} {tf}")
        opens, closes = _dev_panel(symbols, tf, timeframes[tf], cutoffs[tf])
        rotations[tf] = walk_forward_rotation(
            opens, closes, range(0, len(closes)), tf, config.engine_config(), n_folds=config.WFO_FOLDS,
            min_train_fraction=config.WFO_MIN_TRAIN_FRACTION, min_trades=config.WFO_MIN_TRADES,
            cost_multiplier=config.COST_STRESS)
        for k, f in enumerate(rotations[tf]["folds"], 1):
            fold_rows.append({"Symbol": "*", "Timeframe": tf, "Strategy": ROTATION_KEY, "Fold": k,
                              "TestStart": f["test_start"], "Params": f["params"],
                              "TrainSharpe": f.get("train_sharpe"), "TestTrades": f["test_trades"],
                              "TestReturn%": f.get("test_return_pct")})

    pd.DataFrame(result_rows).to_csv(RESULTS_FILE, index=False, float_format="%.6g")
    pd.DataFrame(fold_rows).to_csv(FOLDS_FILE, index=False, float_format="%.6g")
    summary = summarize(per_symbol, rotations, config.PRIOR_TRIALS)
    summary.to_csv(SUMMARY_FILE, index=False, float_format="%.6g")
    print(f"\n📁 Saved {RESULTS_FILE}, {FOLDS_FILE}, {SUMMARY_FILE}, {META_FILE}")
    if not summary.empty:
        cols = ["Strategy", "Timeframe", "Symbols", "Return%", "BuyHold%", "StressReturn%", "Sharpe",
                "BuyHoldSharpe", "MaxDD%", "BuyHoldMaxDD%", "Trades", "DSR", "Pass"]
        with pd.option_context("display.width", 250, "display.max_columns", 30,
                               "display.float_format", "{:.2f}".format):
            print("\n📊 Walk-forward out-of-sample (development data), equal-weight portfolio per candidate:")
            print(summary[cols].to_string(index=False))
        print(f"\nFinalists: {int(summary['Pass'].sum())} (run with --holdout to test them once)")
    return summary


# ---------- holdout ----------
def run_holdout():
    """Test the development finalists once on the held-out data."""
    with open(META_FILE) as f:
        meta = json.load(f)
    summary = pd.read_csv(SUMMARY_FILE)
    finalists = summary[summary["Pass"]]
    if finalists.empty:
        print("No finalists in research_summary.csv; nothing to test on the holdout.")
        return pd.DataFrame()

    cfg = config.engine_config()
    rows = []
    for cand in finalists.itertuples():
        tf, days = cand.Timeframe, meta["timeframes"][cand.Timeframe]
        cutoff = pd.Timestamp(meta["holdout_start"][tf])
        if cand.Strategy == ROTATION_KEY:
            frames = {s: _load(s, tf, days) for s in meta["symbols"]}
            opens, closes = build_panel({s: f for s, f in frames.items() if len(f)})
            start = int((closes.index < cutoff).sum())
            train_len = int(round(start * config.WFO_MIN_TRAIN_FRACTION))
            res = holdout_rotation(opens, closes, range(start - train_len, start),
                                   range(start, len(closes)), tf, cfg, config.WFO_MIN_TRADES)
            eq, trades = res["equity"], res["trades"]
            bh = _portfolio({s: closes[s].iloc[start:].dropna() for s in closes.columns
                             if closes[s].iloc[start:].notna().sum() > 1})
        else:
            curves, bh_curves, trades = {}, {}, 0
            for s in meta["symbols"]:
                df = _load(s, tf, days)
                start = int((df["timestamp"] < cutoff).sum())
                if start < config.RESEARCH_MIN_CANDLES or start >= len(df):
                    continue
                df = add_indicators(df)
                train_len = int(round(start * config.WFO_MIN_TRAIN_FRACTION))
                res = holdout_test(df, cand.Strategy, tf, cfg, range(start - train_len, start),
                                   range(start, len(df)), config.WFO_MIN_TRADES, config.RESEARCH_STOPS)
                curves[s] = res["equity"]
                bh_curves[s] = df.set_index("timestamp")["close"].iloc[start:]
                trades += res["trades"]
            eq, bh = _portfolio(curves), _portfolio(bh_curves)
        eq = eq / eq.iloc[0]
        d, d_bh = _daily_returns(eq), _daily_returns(bh)
        row = {"Strategy": cand.Strategy, "Timeframe": tf, "HoldoutStart": cutoff,
               "Return%": (eq.iloc[-1] - 1) * 100, "BuyHold%": (bh.iloc[-1] - 1) * 100,
               "Sharpe": _sharpe_daily(d) * math.sqrt(365), "BuyHoldSharpe": _sharpe_daily(d_bh) * math.sqrt(365),
               "MaxDD%": max_drawdown_pct(eq), "BuyHoldMaxDD%": max_drawdown_pct(bh), "Trades": trades}
        row["Pass"] = row["Return%"] > 0 and row["Sharpe"] > row["BuyHoldSharpe"]
        rows.append(row)

    out = pd.DataFrame(rows)
    out.to_csv(HOLDOUT_FILE, index=False, float_format="%.6g")
    with pd.option_context("display.width", 250, "display.float_format", "{:.2f}".format):
        print("\n🔓 Holdout (tested once):")
        print(out.to_string(index=False))
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # show progress live even when redirected
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="+")
    ap.add_argument("--timeframes", nargs="+", help="subset of config.RESEARCH_TIMEFRAMES")
    ap.add_argument("--strategies", nargs="*", help="single-asset strategies (default: config.RESEARCH_STRATEGIES)")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--no-fetch", action="store_true", help="use the cache only")
    ap.add_argument("--holdout", action="store_true", help="test the finalists once on the holdout")
    a = ap.parse_args()
    if a.holdout:
        run_holdout()
    else:
        tfs = {tf: config.RESEARCH_TIMEFRAMES[tf] for tf in a.timeframes} if a.timeframes else None
        run(a.symbols, tfs, a.strategies, a.workers, fetch=not a.no_fetch)
