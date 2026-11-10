# Metrics: what the numbers mean

Percentages are in percent (12.5 means 12.5%). Every figure is after fees and
slippage.

## Columns

| Column | Definition | How to read it |
|---|---|---|
| Return% | Final equity / starting equity - 1, with compounding, open position marked at the last close | The headline, but meaningless without BuyHold% and MaxDD% next to it |
| BuyHold% | Last close / first close - 1 over the same candles (portfolio: equal weight across symbols, no costs) | The benchmark. Crypto trends hard, so most long-only rules look good in a bull market |
| StressReturn% | Return% with fees and slippage multiplied by `--cost-stress` (1.5) | If this flips negative, the result lives on optimistic cost assumptions |
| Trades | Closed round trips | Below 30 out-of-sample, trade statistics are noise |
| WinRate% | Share of trades with positive profit after costs | Trend strategies often win 20-40% of trades and still make money; judge it together with profit factor |
| ProfitFactor | Gross profit / gross loss | Above 1 means profitable trades outweigh losing ones. `inf` means no losing trade yet: a small-sample artifact |
| AvgTrade% | Mean return per trade | Compare with the round-trip cost (about 0.3%): a small average trade is fragile |
| MaxDD% | Largest peak-to-trough fall of the equity curve, marked at every close | The loss an investor would have sat through. BuyHoldMaxDD% is the same for holding |
| Sharpe | Mean / standard deviation of returns, annualized, risk-free rate 0 | See below |
| Exposure% | Share of candles with an open position | Low exposure with a similar return means less time at risk |
| DefaultParams% | (walkforward) Return of the default parameters over the same test windows | If optimizing does not beat this, the optimization added nothing |
| TrainSharpe | (walkforward) Median training-window Sharpe of the parameters that were chosen | The in-sample figure. A large gap to the out-of-sample Sharpe is overfitting |
| TrainPositive% | (walkforward) Share of parameter sets with a positive training Sharpe | Low values mean there was little to select from |
| ParamChanges | (walkforward) How often the chosen parameters changed between folds | Frequent changes mean an unstable optimum |
| PSR | Probabilistic Sharpe ratio: probability that the true Sharpe is above 0, given the sample length, skewness and kurtosis of daily returns | Below 0.95 the result is not distinguishable from luck |
| DSR | Deflated Sharpe ratio: PSR against the Sharpe the best of `--trials` skill-less strategies would reach by luck | Use when several ideas were tried. Below 0.95: not convincing |

## Two Sharpe ratios

- **Per-symbol tables** use per-candle equity returns, annualized with the
  number of candles per year (crypto trades every day: 365 for 1d, 2190 for
  4h).
- **Portfolio tables** use daily returns of the equal-weight portfolio,
  annualized with sqrt(365). Compare these with BuyHoldSharpe in the same row.

Rough scale for an annualized Sharpe: below 0.5 weak, 0.5-1 moderate, above 1
good, above 2 on a backtest usually means an error or overfitting. The
uncertainty is large: with T years of data the standard error is about
1/sqrt(T), so a Sharpe of 0.6 measured over two years is statistically
indistinguishable from zero.

## In-sample and out-of-sample

- **backtest**: the period is cut once in time. IS is the first part, OOS the
  last `--oos-fraction` (30%). The cut is the same date for every symbol.
  Indicators are computed on the whole history (they only look backwards) and
  each segment starts flat. OOS is only out-of-sample if the parameters were
  not chosen by looking at it.
- **walkforward**: the first `--min-train-fraction` (40%) is training only. The
  rest is split into `--folds` test windows. For each window, every parameter
  set (grid x stops) is run on the training window before it, the one with the
  best Sharpe (and at least `--min-train-trades` trades) is applied to the test
  window, and the test windows are chained into one out-of-sample curve. If no
  parameter set qualifies, the fold stays flat.

The portfolio curve is the mean of the symbols' per-candle returns, that is,
equal weight rebalanced every candle, for the strategy and for buy and hold
alike. Rebalancing costs are not charged. Per-symbol rows are each symbol on
its own.

## The deflated Sharpe in this tool

With `--trials N`, DSR compares the portfolio's daily Sharpe with the expected
maximum of N independent skill-less strategies, taking the standard error of a
Sharpe estimate as 1/sqrt(number of daily returns). It is an approximation: the
exact method needs the Sharpe of every trial. N should count every strategy
idea whose out-of-sample result has been looked at on this data, including
earlier sessions. Parameter sets searched inside the walk-forward training
windows do not count; they never see the test window.

## Reading a result

A result deserves attention only if all of these hold out-of-sample: return
above zero, still above zero in the cost stress run, Sharpe above buy and hold,
at least 30 trades, PSR (or DSR) at or above 0.95. Missing any one of them is
the normal case, and the report should say which ones failed.

"Lower drawdown than holding, similar or lower return" is a real and common
finding for trend filters: it is risk reduction, not outperformance. Say it
that way.
