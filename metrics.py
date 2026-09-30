# metrics.py
"""Performance metrics for a backtest run (trades + marked-to-market equity curve)."""
import math

import numpy as np
import pandas as pd

from data import timeframe_ms

MS_PER_YEAR = 365 * 24 * 3_600_000  # crypto trades 24/7


def bars_per_year(timeframe):
    return MS_PER_YEAR / timeframe_ms(timeframe)


def max_drawdown_pct(equity):
    """Largest peak-to-trough decline of the equity curve, as a positive percentage."""
    if len(equity) == 0:
        return 0.0
    peak = equity.cummax()
    return float(((peak - equity) / peak).max() * 100)


def sharpe_ratio(equity, timeframe, initial_equity):
    """Annualized Sharpe of per-candle equity returns (risk-free rate = 0)."""
    returns = pd.concat([pd.Series([initial_equity]), equity.reset_index(drop=True)]).pct_change().dropna()
    std = returns.std(ddof=1)
    if len(returns) < 2 or not std > 0:
        return float("nan")
    return float(returns.mean() / std * math.sqrt(bars_per_year(timeframe)))


def exposure_pct(trades, equity):
    """Share of candles with an open position (from entry candle through exit candle)."""
    if len(equity) == 0 or trades.empty:
        return 0.0
    times = equity.index.to_numpy()
    in_pos = np.zeros(len(times), dtype=bool)
    for entry, exit_ in zip(trades["entry_time"], trades["exit_time"]):
        in_pos |= (times >= np.datetime64(entry)) & (times <= np.datetime64(exit_))
    return float(in_pos.mean() * 100)


def compute_metrics(trades, equity, timeframe, initial_equity, closes=None):
    """Summary metrics as a flat dict (percentages are in %, e.g. 12.5 means 12.5%)."""
    n = len(trades)
    pnl = trades["pnl"] if n else pd.Series(dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gross_loss = -losses.sum()

    final = float(equity.iloc[-1]) if len(equity) else initial_equity
    out = {
        "return_pct": (final / initial_equity - 1) * 100,
        "buy_hold_pct": float(closes.iloc[-1] / closes.iloc[0] - 1) * 100 if closes is not None and len(closes) else float("nan"),
        "trades": n,
        "win_rate_pct": len(wins) / n * 100 if n else float("nan"),
        "profit_factor": (wins.sum() / gross_loss if gross_loss > 0 else float("inf")) if n else float("nan"),
        "avg_trade_pct": float(trades["return_pct"].mean()) if n else float("nan"),
        "avg_win_pct": float(trades.loc[pnl > 0, "return_pct"].mean()) if len(wins) else float("nan"),
        "avg_loss_pct": float(trades.loc[pnl <= 0, "return_pct"].mean()) if len(losses) else float("nan"),
        "max_drawdown_pct": max_drawdown_pct(pd.concat([pd.Series([initial_equity]), equity.reset_index(drop=True)])),
        "sharpe": sharpe_ratio(equity, timeframe, initial_equity),
        "exposure_pct": exposure_pct(trades, equity),
        "fees": float(trades["fees"].sum()) if n else 0.0,
    }
    return out
