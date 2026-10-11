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

## Result (run once, 2026-10-12)

| | Period B equal | Period B inverse vol 60d | Period A equal | Period A inverse vol 60d |
|---|---|---|---|---|
| Return | +676.0% | **+841.0%** | −9.2% | **+48.0%** |
| CAGR | 53.9% | 60.3% | −2.4% | 10.3% |
| Sharpe | 1.09 | **1.17** | 0.14 | **0.45** |
| Max drawdown | 55.1% | **54.2%** | 64.0% | **54.0%** |
| Exposure | 43.0% | 43.0% | 41.2% | 42.7% |

All four criteria hold: **PASS**. (The equal-weight rows match the survivorship test exactly.)

- Bootstrap 90% CI of Sharpe(inverse vol) − Sharpe(equal): period B [−0.01, 0.18]
  (P(diff ≤ 0) = 7.8%), period A [0.21, 0.42] (P = 0.0%).
- Lookbacks 30 / 90 days: Sharpe B 1.19 / 1.13, A 0.49 / 0.42. All three beat equal
  weight; the 60-day choice is not changed.
- Ignored rebalancing cost: average one-way monthly weight turnover is 19% (B) and 22%
  (A). Trading it on the ~43% of capital that is invested at fill costs of 0.15% costs
  roughly 0.03% a month (~0.3% a year), far below the differences above.

Interpretation: inverse-volatility weights helped in both periods, clearly in 2022–2026
(when the most volatile small caps collapsed) and modestly in 2018–2022. Equal weight
let the wildest coins dominate the risk; scaling them down kept most of the trend gains.
Because both periods were already used for equal-weight v9, this is supporting evidence,
not an independent test. Next step per the commitments: a paper instance with
inverse-volatility sizing next to `forward-v9`.
