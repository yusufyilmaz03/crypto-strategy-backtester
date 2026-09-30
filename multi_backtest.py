# multi_backtest.py
"""Grid backtest: every symbol x timeframe x strategy, with in-sample, out-of-sample
and walk-forward results written to multi_backtest_strategies.csv."""
import argparse
import sys

import pandas as pd

import config
import strategy
from data import load_ohlcv
from evaluation import evaluate_segment, is_oos_split, walk_forward_splits
from indicators import add_indicators

OUT_FILE = "multi_backtest_strategies.csv"

COLUMN_NAMES = {
    "return_pct": "Return%", "buy_hold_pct": "BuyHold%", "trades": "Trades",
    "win_rate_pct": "WinRate%", "profit_factor": "ProfitFactor", "avg_trade_pct": "AvgTrade%",
    "avg_win_pct": "AvgWin%", "avg_loss_pct": "AvgLoss%",
    "max_drawdown_pct": "MaxDD%", "sharpe": "Sharpe", "exposure_pct": "Exposure%",
    "fees": "Fees", "start": "Start", "end": "End",
}


def segments(n):
    """Named row ranges to evaluate: full history, IS, OOS and walk-forward test folds."""
    is_rows, oos_rows = is_oos_split(n, config.OOS_FRACTION)
    out = [("ALL", range(0, n)), ("IS", is_rows), ("OOS", oos_rows)]
    if config.WALK_FORWARD_FOLDS:
        for k, (_, test) in enumerate(walk_forward_splits(n, config.WALK_FORWARD_FOLDS), 1):
            out.append((f"WF{k}", test))
    return out


def backtest_symbol(symbol, timeframe, strategies, days, engine_cfg):
    df = add_indicators(load_ohlcv(symbol, timeframe, days))
    rows = []
    for key in strategies:
        signals = strategy.get_strategy(key)(df)
        for name, rng in segments(len(df)):
            m, _, _ = evaluate_segment(df, signals, rng, engine_cfg, timeframe, symbol)
            rows.append({"Symbol": symbol, "Timeframe": timeframe, "Strategy": key,
                         "Segment": name, **m})
    return rows


def summarize(results):
    """Per strategy/timeframe: how the edge holds up in-sample vs out-of-sample."""
    r = results[results["Segment"].isin(["IS", "OOS"])].copy()
    r["Excess%"] = r["Return%"] - r["BuyHold%"]
    g = r.groupby(["Strategy", "Timeframe", "Segment"])
    summary = pd.DataFrame({
        "MedianReturn%": g["Return%"].median(),
        "MedianExcess%": g["Excess%"].median(),
        "Profitable%": g["Return%"].apply(lambda x: (x > 0).mean() * 100),
        "MedianPF": g["ProfitFactor"].median(),
        "MedianMaxDD%": g["MaxDD%"].median(),
        "Trades": g["Trades"].sum(),
    }).unstack("Segment")
    summary.columns = [f"{seg} {col}" for col, seg in summary.columns]
    order = [f"{seg} {col}" for seg in ["IS", "OOS"] for col in
             ["MedianReturn%", "MedianExcess%", "Profitable%", "MedianPF", "MedianMaxDD%", "Trades"]]
    return summary[order]


def run(symbols=None, timeframes=None, strategies=None, days=None):
    symbols = symbols or config.SYMBOLS
    timeframes = timeframes or config.TIMEFRAMES
    strategies = strategies or config.STRATEGY_LIST
    days = days or config.BACKTEST_DAYS
    engine_cfg = config.engine_config()

    rows = []
    for symbol in symbols:
        for tf in timeframes:
            print(f"⏳ Testing: {symbol} - {tf} ({days} days)")
            try:
                rows.extend(backtest_symbol(symbol, tf, strategies, days, engine_cfg))
            except Exception as e:
                print(f"[ERROR] {symbol} {tf}: {e}")

    results = pd.DataFrame(rows).rename(columns=COLUMN_NAMES)
    results.to_csv(OUT_FILE, index=False, float_format="%.6g")
    print(f"\n📁 Results saved to '{OUT_FILE}' ({len(results)} rows).")

    if not results.empty:
        with pd.option_context("display.width", 250, "display.max_columns", 20,
                               "display.float_format", "{:.2f}".format):
            print("\n📊 In-sample vs out-of-sample (medians across symbols):")
            print(summarize(results))
    return results


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # show progress live even when redirected
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="+", help="e.g. BTC/USDT ETH/USDT (default: config.SYMBOLS)")
    ap.add_argument("--timeframes", nargs="+", help="e.g. 5m 15m (default: config.TIMEFRAMES)")
    ap.add_argument("--strategies", nargs="+", help="e.g. v1 v6 (default: config.STRATEGY_LIST)")
    ap.add_argument("--days", type=float, help=f"history length (default: {config.BACKTEST_DAYS})")
    a = ap.parse_args()
    run(a.symbols, a.timeframes, a.strategies, a.days)
