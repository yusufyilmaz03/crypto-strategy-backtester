# holdout_test.py
"""One-off holdout test of v9 with fixed parameters (see docs/v9-holdout-test.md)."""
import math

import pandas as pd

import config
import strategy
from data import load_ohlcv
from engine import run_backtest
from indicators import add_indicators
from metrics import max_drawdown_pct

HOLDOUT_START = pd.Timestamp("2025-12-12 19:31:35")   # recorded by the Phase 2b research run
PARAMS = {"fast": 1, "slow": 50}
MIN_HISTORY = 50
MIN_TRADES = 30


def _sharpe(curve):
    d = curve.resample("1D").last().dropna().pct_change().dropna()
    return d.mean() / d.std(ddof=1) * math.sqrt(365) if len(d) > 2 and d.std(ddof=1) > 0 else float("nan")


def main():
    cfg = config.paper_engine_config()
    fn = strategy.get_strategy("v9")
    curves, bh, trades, skipped = {}, {}, [], []
    for s in config.SYMBOLS:
        df = load_ohlcv(s, "1d", 1460 + 30)
        start = int((df["timestamp"] < HOLDOUT_START).sum())
        if start < MIN_HISTORY or start >= len(df):
            skipped.append(s)
            continue
        df = add_indicators(df)
        sig = fn(df, **PARAMS)
        t, eq = run_backtest(df.iloc[start:], sig.iloc[start:], cfg, s)
        curves[s] = eq
        bh[s] = df.set_index("timestamp")["close"].iloc[start:]
        trades.append(t)

    def portfolio(c):
        r = pd.concat({k: v.pct_change() for k, v in c.items()}, axis=1).sort_index()
        return (1 + r.mean(axis=1, skipna=True).fillna(0)).cumprod()

    strat, bench = portfolio(curves), portfolio(bh)
    tr = pd.concat(trades, ignore_index=True)
    closed = tr[tr["reason"] != "END"]
    r = {
        "return": (strat.iloc[-1] - 1) * 100, "bh_return": (bench.iloc[-1] - 1) * 100,
        "sharpe": _sharpe(strat), "bh_sharpe": _sharpe(bench),
        "max_dd": max_drawdown_pct(strat), "bh_max_dd": max_drawdown_pct(bench),
        "closed": len(closed), "win_rate": (closed["pnl"] > 0).mean() * 100,
    }
    print(f"v9 holdout: {strat.index[0]:%Y-%m-%d} → {strat.index[-1]:%Y-%m-%d} "
          f"({(strat.index[-1] - strat.index[0]).days} days), {len(curves)} symbols, skipped: {skipped}")
    print(f"  Return        {r['return']:8.2f}%   buy & hold {r['bh_return']:8.2f}%")
    print(f"  Sharpe        {r['sharpe']:8.2f}    buy & hold {r['bh_sharpe']:8.2f}")
    print(f"  Max drawdown  {r['max_dd']:8.2f}%   buy & hold {r['bh_max_dd']:8.2f}%")
    print(f"  Closed trades {r['closed']:8d}    win rate {r['win_rate']:.1f}%")
    checks = [r["sharpe"] > r["bh_sharpe"], r["max_dd"] < r["bh_max_dd"], r["closed"] >= MIN_TRADES]
    print("  PASS" if all(checks) else f"  FAIL (Sharpe {checks[0]}, drawdown {checks[1]}, trades {checks[2]})")
    return r, strat, bench


if __name__ == "__main__":
    main()
