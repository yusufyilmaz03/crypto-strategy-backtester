# forward_report.py
"""Forward-test report: paper trading results in paper.db vs. equal-weight buy-and-hold
of the same symbols over the same period (see docs/forward-test.md)."""
import math
import sqlite3
import sys

import pandas as pd

import config
from data import load_ohlcv
from metrics import max_drawdown_pct
from store import DB_FILE

MIN_DAYS = 180
MIN_TRADES = 30


def _annual_sharpe(equity):
    daily = equity.resample("1D").last().dropna().pct_change().dropna()
    if len(daily) < 3 or not daily.std(ddof=1) > 0:
        return float("nan")
    return daily.mean() / daily.std(ddof=1) * math.sqrt(365)


def report(label=None, timeframe=None, db=DB_FILE, closes=None):
    label = label or config.strategy_label()
    timeframe = timeframe or config.TIMEFRAME
    with sqlite3.connect(db) as conn:
        eq = pd.read_sql_query("SELECT symbol, time, equity FROM equity WHERE strategy=? AND timeframe=?",
                               conn, params=(label, timeframe))
        trades = pd.read_sql_query("SELECT * FROM trades WHERE strategy=? AND timeframe=? AND exit_time IS NOT NULL",
                                   conn, params=(label, timeframe))
    if eq.empty:
        print(f"No paper data for '{label}' ({timeframe}) in {db}.")
        return None

    eq["time"] = pd.to_datetime(eq["time"])
    wide = eq.pivot_table(index="time", columns="symbol", values="equity").sort_index().ffill()
    wide = wide.fillna(config.INITIAL_EQUITY)
    start, end = wide.index[0], wide.index[-1]
    strat = wide.sum(axis=1) / (config.INITIAL_EQUITY * wide.shape[1])

    if closes is None:
        days = (end - start).days + 60
        closes = pd.concat({s: load_ohlcv(s, timeframe, days).set_index("timestamp")["close"]
                            for s in wide.columns}, axis=1)
    closes = closes.loc[start:end]
    bh = (1 + closes.pct_change().mean(axis=1, skipna=True).fillna(0)).cumprod()

    days = (end - start).days
    r = {
        "run": label, "timeframe": timeframe, "start": start, "end": end, "days": days,
        "return_pct": (strat.iloc[-1] - 1) * 100, "buy_hold_pct": (bh.iloc[-1] - 1) * 100,
        "sharpe": _annual_sharpe(strat), "buy_hold_sharpe": _annual_sharpe(bh),
        "max_dd_pct": max_drawdown_pct(pd.concat([pd.Series([1.0]), strat.reset_index(drop=True)])),
        "buy_hold_max_dd_pct": max_drawdown_pct(pd.concat([pd.Series([1.0]), bh.reset_index(drop=True)])),
        "closed_trades": len(trades),
        "win_rate_pct": (trades["pnl"] > 0).mean() * 100 if len(trades) else float("nan"),
    }
    checks = {
        "Sharpe above buy-and-hold": r["sharpe"] > r["buy_hold_sharpe"],
        "Max drawdown below buy-and-hold": r["max_dd_pct"] < r["buy_hold_max_dd_pct"],
        f"At least {MIN_TRADES} closed trades": r["closed_trades"] >= MIN_TRADES,
    }
    r["pass"] = all(checks.values()) and days >= MIN_DAYS

    print(f"Forward test: {label} ({timeframe}), {start:%Y-%m-%d} → {end:%Y-%m-%d} ({days} days)")
    print(f"  Return        {r['return_pct']:8.2f}%   buy & hold {r['buy_hold_pct']:8.2f}%")
    print(f"  Sharpe        {r['sharpe']:8.2f}    buy & hold {r['buy_hold_sharpe']:8.2f}")
    print(f"  Max drawdown  {r['max_dd_pct']:8.2f}%   buy & hold {r['buy_hold_max_dd_pct']:8.2f}%")
    print(f"  Closed trades {r['closed_trades']:8d}    win rate {r['win_rate_pct']:.1f}%")
    for name, ok in checks.items():
        print(f"  [{'x' if ok else ' '}] {name}")
    if days < MIN_DAYS:
        print(f"  Too early to evaluate: {MIN_DAYS - days} more days needed.")
    else:
        print("  PASS" if r["pass"] else "  FAIL")
    return r


if __name__ == "__main__":
    report(*(sys.argv[1:3]))
