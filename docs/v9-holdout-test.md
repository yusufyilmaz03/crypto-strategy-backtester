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

## Result (run once, 2026-10-11)

2025-12-13 → 2026-10-10 (301 days), 42 symbols, none skipped.

| | v9 | Equal-weight buy-and-hold |
|---|---|---|
| Return | +7.20% | −11.38% |
| Annualized Sharpe (daily) | 0.42 | 0.07 |
| Max drawdown | 24.65% | 48.92% |
| Closed trades | 401 (win rate 11.5%) | |

**PASS** on all three criteria.

Interpretation: the result matches the development pattern. In a falling market v9
stayed positive and roughly halved the drawdown by being out of coins trading below
their 50-day average. The absolute evidence is still modest: a Sharpe of 0.42 over
0.8 years is far from significant on its own, so the robust finding is the risk
reduction (drawdown) rather than the return. The last 10 days overlap with the paper
forward test, which keeps running.
