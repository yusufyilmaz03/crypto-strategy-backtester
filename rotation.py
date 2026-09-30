# rotation.py
"""Cross-sectional momentum rotation (strategy R1) across many symbols.

Every `rebalance` candles, at the close, coins are ranked by their `lookback`-candle
return; the top `top_k` with a positive return get equal weight (1/top_k each), the
rest of the equity stays in cash. The new weights are traded at the next candle's
open. Fees and slippage are charged on the traded value (turnover). Equity is marked
at each close. Long-only, no leverage.
"""
import itertools
from dataclasses import dataclass

import numpy as np
import pandas as pd

ROTATION_GRID = {"lookback": [20, 60, 120], "top_k": [3, 5], "rebalance": [7, 14]}


def rotation_combinations():
    names = list(ROTATION_GRID)
    return [dict(zip(names, v)) for v in itertools.product(*ROTATION_GRID.values())]


def build_panel(frames):
    """{symbol: OHLCV frame} -> (opens, closes) wide frames indexed by timestamp."""
    opens = pd.concat({s: f.set_index("timestamp")["open"] for s, f in frames.items()}, axis=1).sort_index()
    closes = pd.concat({s: f.set_index("timestamp")["close"] for s, f in frames.items()}, axis=1).sort_index()
    return opens, closes


@dataclass
class RotationResult:
    equity: pd.Series      # marked at each close
    trades: int            # position entries (a coin going from 0 to >0 weight)
    fees: float


def simulate(opens, closes, rows, lookback, top_k, rebalance, fee_rate=0.001, slippage_bps=5.0,
             initial_equity=1000.0):
    """Run the rotation over row positions `rows` (a range) of the panel.

    Momentum uses the full panel history before each decision, so a segment can
    start without a warm-up gap; trading only happens inside the segment.
    """
    o = opens.to_numpy(dtype=float)
    c = closes.to_numpy(dtype=float)
    c_mark = closes.ffill().to_numpy(dtype=float)         # for marking through data gaps
    mom = (closes / closes.shift(lookback) - 1).to_numpy(dtype=float)
    cost = fee_rate + slippage_bps / 10_000

    n_sym = c.shape[1]
    qty = np.zeros(n_sym)
    cash = initial_equity
    target = None
    trades, fees = 0, 0.0
    equity = []
    for k, i in enumerate(rows):
        # 1) trade yesterday's decision at this open
        if target is not None:
            px = np.where(np.isnan(o[i]), np.nan, o[i])
            value_now = qty * np.nan_to_num(px, nan=0.0)
            # holdings without an open price this candle cannot be traded: keep them
            frozen = (qty > 0) & np.isnan(px)
            tradable = ~np.isnan(px)
            total = cash + np.nansum(qty * np.where(frozen, c_mark[i - 1], np.nan_to_num(px)))
            target_value = np.where(tradable, target * total, 0.0)
            delta = np.where(tradable, target_value - value_now, 0.0)
            fee = np.abs(delta).sum() * cost
            fees += fee
            trades += int(((qty == 0) & (target_value > 0)).sum())
            new_qty = np.where(tradable, target_value / np.where(tradable, px, 1.0), qty)
            cash = cash - delta.sum() - fee
            qty = np.where(new_qty > 1e-12, new_qty, 0.0)
            target = None

        # 2) mark at the close
        equity.append(cash + np.nansum(qty * c_mark[i]))

        # 3) rebalance decision at this close
        if k % rebalance == 0:
            m = mom[i]
            valid = ~np.isnan(m) & ~np.isnan(c[i]) & (m > 0)
            order = np.argsort(-np.where(valid, m, -np.inf))[:top_k]
            w = np.zeros(n_sym)
            chosen = [j for j in order if valid[j]]
            w[chosen] = 1.0 / top_k
            target = w

    eq = pd.Series(equity, index=closes.index[rows.start:rows.stop], name="equity")
    return RotationResult(eq, trades, fees)


def walk_forward_rotation(opens, closes, rows, timeframe, config, n_folds=5, min_train_fraction=0.4,
                          min_trades=10, cost_multiplier=1.5):
    """Walk-forward optimization of the rotation grid over panel rows `rows`.

    Same scheme as optimize.walk_forward_optimize: parameters are chosen by training
    Sharpe (with a minimum number of entries) and applied to the next test window;
    test windows are chained with compounding.
    """
    from evaluation import walk_forward_splits
    from metrics import sharpe_ratio
    from optimize import _chain

    init = config.initial_equity
    kw = dict(fee_rate=config.fee_rate, slippage_bps=config.slippage_bps, initial_equity=init)
    stress = dict(kw, fee_rate=config.fee_rate * cost_multiplier,
                  slippage_bps=config.slippage_bps * cost_multiplier)
    combos = rotation_combinations()

    def shift(r):
        return range(rows.start + r.start, rows.start + r.stop)

    oos, oos_stress, folds, trades, n_trials = [], [], [], 0, 0
    splits = walk_forward_splits(len(rows), n_folds, min_train_fraction, anchored=False)
    for train, test in splits:
        train, test = shift(train), shift(test)
        best = None
        for p in combos:
            res = simulate(opens, closes, train, **p, **kw)
            n_trials += 1
            if res.trades < min_trades:
                continue
            score = sharpe_ratio(res.equity, timeframe, init)
            if score == score and (best is None or score > best[0]):
                best = (score, p)
        if best is None:
            eq = pd.Series(init, index=closes.index[test.start:test.stop], dtype=float)
            oos.append(eq)
            oos_stress.append(eq)
            folds.append({"test_start": closes.index[test.start], "params": None, "test_trades": 0})
            continue
        res = simulate(opens, closes, test, **best[1], **kw)
        oos.append(res.equity)
        oos_stress.append(simulate(opens, closes, test, **best[1], **stress).equity)
        trades += res.trades
        folds.append({"test_start": closes.index[test.start], "params": best[1],
                      "train_sharpe": best[0], "test_trades": res.trades,
                      "test_return_pct": (res.equity.iloc[-1] / init - 1) * 100})

    first_test = rows.start + splits[0][1].start
    return {
        "oos_equity": _chain(oos, init),
        "high_cost_equity": _chain(oos_stress, init),
        "trades": trades,
        "folds": folds,
        "n_trials": n_trials,
        "bh_closes": closes.iloc[first_test:rows.stop],
    }
