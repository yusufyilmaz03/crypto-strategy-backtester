# Forward test: v9 daily moving-average regime (paper trading)

Written before the forward test starts. The rules and criteria below are fixed.

## Why

No strategy passed Phase 2 or Phase 2b (see `phase2b-preregistration.md`). The
closest candidate, v9 on daily candles, was positive and cost-robust out of sample
with a much smaller drawdown than buy-and-hold, but the evidence was statistically
weak (deflated Sharpe 0.01, t-statistic about 0.86). Paper trading it forward
produces genuinely new out-of-sample data without risking money.

## Setup (config.py)

- Strategy v9 with `fast=1, slow=50`: long while the daily close is above its
  50-day SMA, exit when it closes below. No stop (`PAPER_STOPLOSS_MODE = "none"`).
  This was the parameter set most often chosen by the walk-forward optimization.
- Timeframe 1d, the 42 symbols in `config.SYMBOLS`, 1000 USDT paper equity per
  symbol, fees 0.1% and slippage 5 bps per fill, long-only.
- Run: `python run_realtime.py`. The start date is the first candle stored in
  `paper.db` for this run.

## Evaluation

- Earliest evaluation: 180 days after the start. `python forward_report.py`
  computes the numbers.
- Benchmark: equal-weight buy-and-hold of the same symbols over the same period.
- Pass, all of:
  - daily Sharpe (annualized) above the benchmark's
  - max drawdown below the benchmark's
  - at least 30 closed trades
- Parameters and rules are not changed during the test. If a code change alters
  trading behavior, the test restarts and the change is noted here.

Passing is necessary but not sufficient: six months is short. Together with the
Phase 2b development result it would justify moving to the testnet phase, which
needs explicit approval before any order-sending code is written.
