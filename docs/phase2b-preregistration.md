# Phase 2b pre-registration: trend and momentum strategies

Written before any Phase 2b backtest was run. The design and pass criteria below
are fixed; results are reported against them as they are.

## Context

Phase 2 tested seven RSI/EMA/Bollinger/Donchian strategies on 15m/1h/4h with
walk-forward optimization. No candidate passed: out-of-sample portfolios trailed
buy-and-hold and every candidate turned negative with costs × 1.5. Phase 2b tests
strategy families that aim to beat buy-and-hold on risk (drawdown, Sharpe) rather
than on return.

## Strategies (long-only)

| Key | Rule | Grid |
|---|---|---|
| v8 | Time-series momentum: long while the L-bar return is > 0, exit when ≤ 0 | L ∈ {20, 60, 120} |
| v9 | SMA regime: long while SMA(fast) > SMA(slow), exit below (fast = 1 is the price) | (fast, slow) ∈ {(1,50), (1,100), (1,200), (10,50), (20,100), (50,200)} |
| v10 | Turtle/Donchian: enter when the close breaks the prior N-bar high, exit below the prior M-bar low | (N, M) ∈ {(20,10), (55,20), (55,10), (100,50), (100,20)} |
| R1 | Cross-sectional rotation (1d only): every H bars rank coins by L-bar return, hold the top K with return > 0 in equal weight, else cash. Decisions at the close, trades at the next open, costs on turnover | L ∈ {20, 60, 120}, K ∈ {3, 5}, H ∈ {7, 14} |

Single-asset strategies also choose a stop from {none, ATR × 2, ATR × 3}.

## Data and procedure

- Timeframes: 4h (730 days) and 1d (1460 days), the same 42 symbols. The list
  contains coins that are still traded today, so it has survivorship bias in
  favour of long strategies and buy-and-hold.
- Holdout: the last 20% of each timeframe's history is not used during research.
- Development: rolling walk-forward optimization (5 folds, first 40% training only,
  training objective Sharpe with at least 10 trades), equal-weight portfolio of the
  symbols' out-of-sample curves, compared with an equal-weight buy-and-hold portfolio.

## Pass criteria

Development (walk-forward out-of-sample, holdout excluded), all of:

- return > 0 and return with fees and slippage × 1.5 > 0
- Sharpe > buy-and-hold Sharpe
- at least 30 trades
- deflated Sharpe ratio ≥ 0.95, with the number of trials counting all candidates
  tested so far (21 from Phase 2 + 7 here = 28)

Holdout (finalists only, evaluated once): return > 0 and Sharpe > buy-and-hold Sharpe.
