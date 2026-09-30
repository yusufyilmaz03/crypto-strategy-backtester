# optimize.py
"""Walk-forward optimization (WFO) for one strategy on one symbol/timeframe.

For each fold, every parameter combination (strategy grid x stop multiplier) is
backtested on the training window; the best one by training Sharpe (with a
minimum number of trades) is then run on the following test window. Only the
test windows count as out-of-sample. Test-window equity curves are chained with
compounding into one out-of-sample curve.
"""
from dataclasses import replace

import pandas as pd

import strategy
from engine import run_backtest
from evaluation import walk_forward_splits
from metrics import compute_metrics

STOP_MULTIPLIERS = [1.5, 2.0, 3.0]


def _score(trades, equity, timeframe, initial_equity, min_trades):
    """Training objective: Sharpe, or None when there are too few trades to judge."""
    if len(trades) < min_trades:
        return None
    m = compute_metrics(trades, equity, timeframe, initial_equity)
    return m["sharpe"] if m["sharpe"] == m["sharpe"] else None  # NaN -> None


def _chain(parts, initial_equity):
    """Chain per-window equity curves (each starting at initial_equity) with compounding."""
    level, chained = initial_equity, []
    for eq in parts:
        chained.append(eq / initial_equity * level)
        level = level * eq.iloc[-1] / initial_equity if len(eq) else level
    return pd.concat(chained) if chained else pd.Series(dtype=float)


def walk_forward_optimize(df, key, timeframe, config, n_folds=5, min_train_fraction=0.4,
                          anchored=False, min_trades=10, stops=STOP_MULTIPLIERS, cost_multiplier=1.5):
    """Run WFO and return a dict with OOS metrics, per-fold choices and comparisons.

    df must already contain indicators. Signals for every combination are computed
    once on the full history (they only look backwards) and sliced per window.
    """
    fn = strategy.get_strategy(key)
    combos = strategy.param_combinations(key)
    signals = [fn(df, **p) for p in combos]
    default_signals = fn(df)
    cfgs = {s: replace(config, atr_multiplier=s) for s in stops}
    init = config.initial_equity

    def run(sig, rows, cfg):
        part = df.iloc[rows.start:rows.stop]
        return run_backtest(part, sig.iloc[rows.start:rows.stop], cfg)

    folds, oos_trades, oos_equity, default_equity, high_cost_equity = [], [], [], [], []
    train_positive = 0
    n_trials = 0
    for train, test in walk_forward_splits(len(df), n_folds, min_train_fraction, anchored):
        best = None
        for i, params in enumerate(combos):
            for stop, cfg in cfgs.items():
                trades, equity = run(signals[i], train, cfg)
                score = _score(trades, equity, timeframe, init, min_trades)
                n_trials += 1
                if score is not None and score > 0:
                    train_positive += 1
                if score is not None and (best is None or score > best[0]):
                    best = (score, i, stop)

        if best is None:
            # Nothing traded enough in training: stay flat in this test window.
            test_trades = pd.DataFrame(columns=["pnl", "return_pct", "fees", "entry_time", "exit_time"])
            test_equity = pd.Series(init, index=df["timestamp"].iloc[test.start:test.stop].values, dtype=float)
            chosen = None
            high_cost_equity.append(test_equity)
        else:
            _, i, stop = best
            test_trades, test_equity = run(signals[i], test, cfgs[stop])
            chosen = {**combos[i], "stop": stop}
            # Same choice with higher costs: does the edge survive worse fills?
            costly = replace(cfgs[stop], fee_rate=config.fee_rate * cost_multiplier,
                             slippage_bps=config.slippage_bps * cost_multiplier)
            high_cost_equity.append(run(signals[i], test, costly)[1])
        oos_trades.append(test_trades)
        oos_equity.append(test_equity)
        default_equity.append(run(default_signals, test, config)[1])

        folds.append({
            "train_start": df["timestamp"].iloc[train.start], "test_start": df["timestamp"].iloc[test.start],
            "test_end": df["timestamp"].iloc[test.stop - 1], "params": chosen,
            "train_sharpe": best[0] if best else None, "test_trades": len(test_trades),
            "test_return_pct": (test_equity.iloc[-1] / init - 1) * 100,
        })

    first_test = walk_forward_splits(len(df), n_folds, min_train_fraction, anchored)[0][1].start
    closes = df["close"].iloc[first_test:]
    trades = pd.concat([t for t in oos_trades if len(t)], ignore_index=True) if any(len(t) for t in oos_trades) \
        else oos_trades[0]
    equity = _chain(oos_equity, init)
    oos = compute_metrics(trades, equity, timeframe, init, closes)
    default = compute_metrics(pd.DataFrame(columns=trades.columns), _chain(default_equity, init),
                              timeframe, init, closes)
    return {
        "oos": oos,
        "oos_equity": equity,
        "oos_trades": trades,
        "default_return_pct": default["return_pct"],
        "high_cost_equity": _chain(high_cost_equity, init),
        "high_cost_return_pct": (_chain(high_cost_equity, init).iloc[-1] / init - 1) * 100,
        "default_sharpe": default["sharpe"],
        "folds": folds,
        "n_trials": n_trials,
        "train_positive_share": train_positive / n_trials if n_trials else float("nan"),
    }
