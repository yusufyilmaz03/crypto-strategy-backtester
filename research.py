# research.py
"""Strategy research: walk-forward optimization for every strategy on every symbol and
research timeframe, then equal-weight portfolio results per strategy/timeframe, compared
with buy-and-hold and checked with a deflated Sharpe ratio across all candidates.

Outputs: research_results.csv (per symbol), research_folds.csv (chosen parameters per
fold) and research_summary.csv (one row per strategy/timeframe candidate).
"""
import argparse
import sys
import math
import os
from multiprocessing import Pool

import pandas as pd

import config
from data import load_ohlcv
from indicators import add_indicators
from metrics import deflated_sharpe, max_drawdown_pct
from optimize import walk_forward_optimize

RESULTS_FILE = "research_results.csv"
FOLDS_FILE = "research_folds.csv"
SUMMARY_FILE = "research_summary.csv"

# Pass criteria for a candidate (strategy + timeframe) portfolio
MIN_TOTAL_TRADES = 30
MIN_DSR = 0.95


def prefetch(symbols, timeframes):
    """Download/refresh the cache sequentially so workers never hit the network."""
    for tf, days in timeframes.items():
        for symbol in symbols:
            try:
                n = len(load_ohlcv(symbol, tf, days))
                print(f"📥 {symbol} {tf}: {n} candles")
            except Exception as e:
                print(f"[ERROR] download {symbol} {tf}: {e}")


def _run_task(task):
    symbol, tf, days, strategies = task
    cfg = config.engine_config()
    df = load_ohlcv(symbol, tf, days, refresh=False)
    if len(df) < config.RESEARCH_MIN_CANDLES:
        return symbol, tf, [], f"only {len(df)} candles"
    df = add_indicators(df)
    out = []
    for key in strategies:
        res = walk_forward_optimize(
            df, key, tf, cfg, n_folds=config.WFO_FOLDS,
            min_train_fraction=config.WFO_MIN_TRAIN_FRACTION,
            min_trades=config.WFO_MIN_TRADES, cost_multiplier=config.COST_STRESS)
        first_test = res["oos_equity"].index[0]
        closes = df.set_index("timestamp")["close"].loc[first_test:]
        out.append((key, res, closes))
    return symbol, tf, out, None


def _daily_returns(equity):
    daily = equity.resample("1D").last().dropna()
    return daily.pct_change().dropna()


def _portfolio(curves):
    """Equal-weight portfolio of per-symbol curves (each rebased), as an equity curve."""
    rets = pd.concat({k: c.pct_change() for k, c in curves.items()}, axis=1).sort_index()
    return (1 + rets.mean(axis=1, skipna=True).fillna(0)).cumprod()


def _sharpe_daily(daily):
    if len(daily) < 3 or not daily.std(ddof=1) > 0:
        return float("nan")
    return daily.mean() / daily.std(ddof=1)


def summarize(per_symbol):
    """Build one row per (strategy, timeframe) candidate from the per-symbol WFO results."""
    rows, daily = [], {}
    for (key, tf), items in per_symbol.items():
        eq = _portfolio({s: r["oos_equity"] for s, r, _ in items})
        eq_cost = _portfolio({s: r["high_cost_equity"] for s, r, _ in items})
        bh = _portfolio({s: c for s, _, c in items})
        d, d_bh = _daily_returns(eq), _daily_returns(bh)
        daily[(key, tf)] = d
        sym_returns = pd.Series([r["oos"]["return_pct"] for _, r, _ in items])
        rows.append({
            "Strategy": key, "Timeframe": tf, "Symbols": len(items),
            "OOSStart": eq.index[0], "OOSEnd": eq.index[-1],
            "Return%": (eq.iloc[-1] - 1) * 100,
            "BuyHold%": (bh.iloc[-1] - 1) * 100,
            "StressReturn%": (eq_cost.iloc[-1] - 1) * 100,
            "Sharpe": _sharpe_daily(d) * math.sqrt(365),
            "BuyHoldSharpe": _sharpe_daily(d_bh) * math.sqrt(365),
            "MaxDD%": max_drawdown_pct(eq),
            "BuyHoldMaxDD%": max_drawdown_pct(bh),
            "Trades": int(sum(r["oos"]["trades"] for _, r, _ in items)),
            "MedianSymbolReturn%": sym_returns.median(),
            "SymbolsPositive%": (sym_returns > 0).mean() * 100,
            "MedianDefaultReturn%": pd.Series([r["default_return_pct"] for _, r, _ in items]).median(),
            "TrainPositiveShare%": pd.Series([r["train_positive_share"] for _, r, _ in items]).mean() * 100,
        })

    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    # Deflated Sharpe: the best of N candidates is compared with what luck alone would give.
    srs = pd.Series({k: _sharpe_daily(d) for k, d in daily.items()}).dropna()
    sr_std = srs.std(ddof=1) if len(srs) > 1 else 0.0
    summary["DSR"] = [deflated_sharpe(daily[(r.Strategy, r.Timeframe)], len(summary), sr_std)
                      for r in summary.itertuples()]
    summary["Pass"] = ((summary["Return%"] > 0) & (summary["StressReturn%"] > 0)
                       & (summary["Sharpe"] > summary["BuyHoldSharpe"])
                       & (summary["Trades"] >= MIN_TOTAL_TRADES) & (summary["DSR"] >= MIN_DSR))
    return summary.sort_values("Sharpe", ascending=False).reset_index(drop=True)


def run(symbols=None, timeframes=None, strategies=None, workers=None, fetch=True):
    symbols = symbols or config.SYMBOLS
    timeframes = timeframes or config.RESEARCH_TIMEFRAMES
    strategies = strategies or config.STRATEGY_LIST
    if fetch:
        prefetch(symbols, timeframes)

    tasks = [(s, tf, days, strategies) for tf, days in timeframes.items() for s in symbols]
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    result_rows, fold_rows, per_symbol = [], [], {}
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

    pd.DataFrame(result_rows).to_csv(RESULTS_FILE, index=False, float_format="%.6g")
    pd.DataFrame(fold_rows).to_csv(FOLDS_FILE, index=False, float_format="%.6g")
    summary = summarize(per_symbol)
    summary.to_csv(SUMMARY_FILE, index=False, float_format="%.6g")
    print(f"\n📁 Saved {RESULTS_FILE}, {FOLDS_FILE}, {SUMMARY_FILE}")
    if not summary.empty:
        cols = ["Strategy", "Timeframe", "Symbols", "Return%", "BuyHold%", "StressReturn%", "Sharpe",
                "BuyHoldSharpe", "MaxDD%", "BuyHoldMaxDD%", "Trades", "SymbolsPositive%", "DSR", "Pass"]
        with pd.option_context("display.width", 250, "display.max_columns", 30,
                               "display.float_format", "{:.2f}".format):
            print("\n📊 Walk-forward out-of-sample, equal-weight portfolio per candidate:")
            print(summary[cols].to_string(index=False))
    return summary


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # show progress live even when redirected
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="+")
    ap.add_argument("--timeframes", nargs="+", help="subset of config.RESEARCH_TIMEFRAMES")
    ap.add_argument("--strategies", nargs="+")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--no-fetch", action="store_true", help="use the cache only")
    a = ap.parse_args()
    tfs = {tf: config.RESEARCH_TIMEFRAMES[tf] for tf in a.timeframes} if a.timeframes else None
    run(a.symbols, tfs, a.strategies, a.workers, fetch=not a.no_fetch)
