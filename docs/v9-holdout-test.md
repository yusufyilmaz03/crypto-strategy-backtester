# v9 holdout test (decided 2026-10-11, written before running it)

## Decision and deviation

The Phase 2b pre-registration reserved the holdout for finalists only, and no
candidate became a finalist. We decided afterwards to spend the 1d holdout on the
closest candidate, v9, instead of waiting 180 days for the paper forward test.
This is a deviation from the pre-registration. Known caveats:

1. The decision to use the holdout for v9 was made after seeing development results.
2. v9's holdout result on BTC, ETH, SOL and DOGE was seen during a code smoke test
   (see the deviation log in `phase2b-preregistration.md`).
3. After this test the 1d holdout is no longer unseen data.

## Fixed test design

- Strategy: v9 with `fast=1, slow=50`, no stop (the forward-test settings). No
  parameter selection.
- Data: 1d candles of the 42 symbols in `config.SYMBOLS`. The holdout boundary is
  the one recorded by the Phase 2b run, 2025-12-12 19:31 UTC: the first holdout
  candle is 2025-12-13, the last is the latest closed daily candle when the test runs.
- Symbols need at least 50 daily candles before the holdout so the SMA exists on
  day one. Signals use the full preceding history; trading starts flat on the first
  holdout candle. Costs as in `config.py` (fees 0.1%, slippage 5 bps per fill).
- Portfolio: equal weight across symbols; benchmark: equal-weight buy-and-hold of the
  same symbols over the same candles.
- Pass, all of (same as `forward-test.md`): annualized daily Sharpe above the
  benchmark's, max drawdown below the benchmark's, at least 30 closed trades.
- The test is run once. Whatever the result, v9's parameters are not changed based on it.
