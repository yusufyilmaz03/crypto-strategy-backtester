# Pitfalls: lookahead, unclosed candles, overfitting

Read this before writing or changing strategy code, and before reporting a
result that looks good.

## 1. Lookahead bias

The signal for candle i may use candles 0..i only. Anything that lets a later
candle influence an earlier signal produces results that cannot be traded.

| Pattern | Why it leaks | Use instead |
|---|---|---|
| `close.shift(-1)`, any negative shift | Reads the next candle | `shift(1)` or more |
| `rolling(n, center=True)` | Half the window is in the future | `rolling(n)` |
| `bfill()`, `fillna(method="bfill")` | Copies later values backwards | `ffill()` or leave NaN during warm-up |
| `close.max()`, `.min()`, `.mean()`, `.std()`, `.quantile()` on the whole series | The statistic includes the future | `rolling(n)` or `expanding()` versions |
| Scaling or z-scoring with whole-sample mean and standard deviation | Same | Rolling or expanding statistics |
| `df.iloc[-1]`, "the latest value" | Is the last candle of the dataset, not of the moment | Row-wise series only |
| Fitting anything (thresholds, a model) on all the data, then generating signals on the same data | The fit saw the future | Fit inside the walk-forward training window only |
| Resampling to a higher timeframe and joining back without a shift | The daily value of a day is known only at its close | Shift the higher-timeframe series by one of its own candles before joining |
| Entering at the close of the candle that produced the signal | The close is not known until it is over | The engine already fills at the next open; do not change that |
| Using the candle's high or low to decide an entry on the same candle | The extremes are known only at the close | Act on the next candle |

The checks (`scripts/backtest.py check`) change or remove the candles after a
cut and require that every signal up to the cut stays the same. They run on
synthetic candles and on the real ones. They are mandatory for custom
strategies: `backtest` and `walkforward` rerun them and stop with exit code 3
on a failure.

Limits of the checks: they cannot see a rule that never fires, a leak that only
appears under rare conditions, or information taken from outside the candle
data. Passing means "no lookahead found", so the source still has to be read.

## 2. Unclosed candles

The newest candle of an exchange feed is still forming; its close, high, low
and volume change until the period ends. A signal computed on it can vanish
before the candle closes.

- The data loader drops every candle whose close time is in the future. Do not
  add data from another source without the same filter.
- The engine acts on a closed candle's signal at the next candle's open.
- A backtest that ends today has an open position marked at the last closed
  candle, not at the live price.
- A candle's timestamp is its open time. The 1d candle stamped 2026-10-31
  closes at 2026-11-01 00:00 UTC.

## 3. Overfitting

Every parameter value, filter and variant that is tried on the same data is
another draw from the noise. The best of many draws looks good by construction.

Rules:

1. Decide the rule and the test before looking at results. Write them down in
   the answer.
2. Tune only inside `walkforward`. Never run `backtest` several times and keep
   the best run.
3. Keep grids small (the built-in grids have 3 to 36 combinations). Prefer
   round, conventional values (20, 50, 200) over fitted ones (37, 143).
4. Count the ideas. If this is the Nth strategy tested on this data, pass
   `--trials N` and report the deflated Sharpe.
5. Prefer results that hold across symbols and across neighbouring parameter
   values over a single sharp optimum.
6. After a failed test, do not "fix" the strategy with a filter that removes
   the losing period. If a new variant is tested, say that it was designed
   after seeing the result and count it as another trial.
7. Do not shorten or shift the period, or drop symbols, to improve a result.
   If the period or the symbols change, report both runs.

Signs of overfitting in the output, all of which the tool prints as warnings:
Sharpe much higher in-sample than out-of-sample; chosen parameters changing
from fold to fold; optimized parameters not beating the defaults; few trades;
a result that dies in the cost stress run; PSR or DSR below 0.95.

## 4. Other biases

- **Survivorship bias**: a symbol list chosen today contains only coins that
  still trade. Delisted and collapsed coins are missing, which flatters both
  the strategy and buy and hold. State this in every report with more than a
  couple of majors.
- **Regime dependence**: two years of crypto can be one bull or one bear
  market. A long-only trend rule looks brilliant in one and useless in the
  other. Give the period and what buy and hold did in it.
- **Costs**: the defaults (0.1% fee, 5 bps slippage per fill) suit liquid
  majors on daily candles. Small coins and short timeframes cost more, and on
  1m-15m candles costs usually dominate. Look at StressReturn%.
- **Fills**: one fill per order at the next open, any size, no partial fills.
  Stops fill at the stop price, or at the open when the candle gaps through
  it. Real stops slip more in fast markets.
- **Indicator warm-up**: the first candles have no signal (an SMA 200 needs
  200). With a short history most of the "in-sample" segment may be warm-up.

## Checklist before reporting

- [ ] Built-in strategy used, or the custom strategy passed `check`
- [ ] The rule and parameters were fixed before the run, or tuned only by `walkforward`
- [ ] Out-of-sample figures lead; buy and hold is next to them
- [ ] Trades, win rate, profit factor, max drawdown, Sharpe reported
- [ ] Cost stress result reported
- [ ] Every tool warning passed on in plain words
- [ ] Number of ideas tried is stated when it is more than one
- [ ] "Research and education only. Not financial advice." is in the answer
