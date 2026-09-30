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

## Deviation log

- While smoke-testing the holdout code path on 4 symbols (BTC, ETH, SOL, DOGE),
  v9 and R1 were artificially marked as finalists, so their holdout results on
  those 4 symbols were seen before the full development run. Nothing in the design,
  grids, criteria or holdout boundary was changed afterwards. If v9 or R1 become
  finalists, their holdout result is reported with this caveat.

## Results (development run, 2026-09-30)

Holdout boundaries: 4h from 2026-05-07, 1d from 2025-12-12. Symbols with fewer than
500 development candles were skipped (6 on 1d).

| Candidate | Symbols | Return | Buy & hold | Costs × 1.5 | Sharpe (B&H) | Max DD (B&H) | Trades | DSR |
|---|---|---|---|---|---|---|---|---|
| v9 1d | 36 | +36.7% | −26.6% | +32.5% | 0.62 (0.19) | 41% (72%) | 684 | 0.01 |
| v8 1d | 36 | +19.0% | −26.6% | +14.0% | 0.43 (0.19) | 42% (72%) | 949 | 0.01 |
| R1 1d | 42 | −11.0% | −25.5% | −16.3% | 0.41 (0.21) | 79% (74%) | 131 | 0.00 |
| v10 1d | 36 | +1.2% | −26.6% | +1.2% | 0.39 (0.19) | 1% (72%) | 13 | 0.00 |
| v10 4h | 42 | −26.1% | −61.0% | −28.6% | −1.31 (−0.95) | 30% (73%) | 923 | 0.00 |
| v9 4h | 42 | −40.9% | −61.0% | −45.0% | −1.57 (−0.95) | 52% (73%) | 1965 | 0.00 |
| v8 4h | 42 | −48.5% | −61.0% | −53.4% | −1.91 (−0.95) | 61% (73%) | 2706 | 0.00 |

**No candidate passed; there are no finalists, so the holdout stays unused.**

v9 1d and v8 1d meet every criterion except the deflated Sharpe ratio. v9 1d
beat buy-and-hold over roughly 700 days mainly by being out of the market during
declines, and it survives the cost stress. But its Sharpe of 0.62 over about 1.9
years corresponds to a t-statistic of roughly 0.86, and even before deflation the
probabilistic Sharpe is well below 0.95. Across walk-forward folds the result is
uneven (one strongly positive fold, the others flat or negative). The most chosen
parameter set was close > SMA(50) without a stop (110 of 180 symbol-folds).
