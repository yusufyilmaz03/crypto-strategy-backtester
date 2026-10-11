# v9 inverse-volatility sizing test (written 2026-10-12, before running it)

## Why

The survivorship-free test showed that v9's edge is risk reduction: it keeps pace in
rising markets and loses much less in crashes. Today every coin gets the same capital,
so the most volatile coins dominate the portfolio's risk. Weighting coins by the
inverse of their volatility is the standard way to spread risk more evenly; if v9's
edge is about risk, this should raise its Sharpe and lower its drawdown.

## Status of the data (read this first)

No unseen daily data is left: the 1d holdout is spent and both periods of the
survivorship test have now been used for equal-weight v9. This test changes only the
weighting, with one fixed, conventional parameter and no tuning, but it is still a new
trial on data we have seen. Its result is weaker evidence than the survivorship test;
the paper forward test stays the independent check.

## Fixed design

- Everything as in `docs/v9-survivorship-test.md`: point-in-time top-50 universe,
  v9 `fast=1, slow=50` with no stop, costs from `config.py`, periods B (2018-01 →
  2022-09) and A (2022-10 → 2026-09), the same join/leave/delisting costs.
- **Equal weight (baseline):** each month's members get the same weight.
- **Inverse volatility (tested):** at each month start a member's weight is proportional
  to 1/σ, σ being the standard deviation of the asset's daily close-to-close returns
  over the **60 days** ending the day before the month. Weights are normalized across
  the month's members and stay fixed during the month. A coin's weight does not depend
  on whether v9 holds it (no information from the strategy).
- Rebalancing between continuing members is not charged for either sizing (as before).
  The average monthly weight turnover of inverse volatility is reported to show the size
  of that ignored cost.

## Pass criteria (all must hold)

1. Sharpe of inverse volatility above equal weight's in period B.
2. Sharpe of inverse volatility above equal weight's in period A.
3. Max drawdown of inverse volatility not above equal weight's in period B.
4. Max drawdown of inverse volatility not above equal weight's in period A.

## Reported, not pass/fail

- Stationary bootstrap 90% CI of Sharpe(inverse vol) − Sharpe(equal weight) per period
  (mean block 20 days, 5000 resamples, seed 0).
- 30 and 90 day lookbacks. They are never used to pick a different lookback.

## Commitments

- The test runs once; the code was tested on synthetic data only. The 60-day lookback
  does not change because of the result.
- PASS: inverse-volatility sizing becomes the candidate sizing for v9. It would first run
  as a new paper instance next to `forward-v9` (the forward test itself stays as it is);
  testnet and live use need the bot's sizing changed and the user's approval.
- FAIL: v9 keeps equal weights.
