# v9 exit variants test (written 2026-10-25, before running it)

## Why

In its first 25 days the forward test (`forward-v9`, started 2026-09-30) lost 10.6%
while equal-weight buy-and-hold lost 14.1%. All 53 closed trades lost money. 23 of them
had been more than 3% in profit (on daily closes) before v9's exit, close < SMA50, sold
them at a loss, and 16 trades lasted 1–3 days (the close crossed the SMA and came
straight back). This raises two questions about the exit and entry rules:

1. **Trailing exit:** would locking in part of an open gain, instead of waiting for the
   close to fall below the SMA50, improve v9?
2. **Entry band:** would asking the close to clear the SMA50 by a margin cut the
   1–3 day whipsaw trades?

These questions come from 25 days of one falling market: 42 highly correlated coins,
so effectively one observation. They can't be answered from that data. This document
fixes the test before it runs.

## Status of the data (read this first)

No unseen daily data is left: the 1d holdout is spent and both periods of the
survivorship test have been used for v9 (equal weight and inverse volatility). This test
changes one rule at a time with fixed, conventional values, but it is a new trial on
data we have seen, so its result is supporting evidence, not an independent test. The
forward data that motivated it (2026-09-30 on) is not part of either period.

## Fixed design

Everything as in `docs/v9-survivorship-test.md`: point-in-time top-50 universe, equal
weight, periods B (2018-01 → 2022-09) and A (2022-10 → 2026-09), signal on the close,
fill at the next open, fees 0.10% + slippage 5 bps per fill, the same join, leave and
delisting costs. ATR is the project's ATR(14) with Wilder smoothing (`add_indicators`).

- **Baseline:** v9 `fast=1, slow=50`, no stop.
- **T, trailing exit (chandelier):** the baseline, plus: while in a position, the engine
  tracks the highest close since entry (the entry candle's close included). When a
  candle closes below that highest close − **3** × ATR(14) of the same candle, the
  position is sold at the next open. The v9 exit (close < SMA50) still applies. After a
  trailing exit the next entry needs the v9 BUY signal and one of: a candle since the
  exit whose v9 signal was not BUY (the close went back to or below the SMA50, the
  regime reset), or a close above the exited trade's highest close (the trend made a new
  high). Without this rule v9 would buy back on the next candle, since its BUY signal
  holds on every candle above the SMA.
- **H, entry band:** enter when the close is above SMA50 × **1.02**; exit as the baseline
  (close < SMA50). Between the two levels nothing changes: a position is kept, no new
  one is opened.

## Pass criteria (per variant, all must hold)

1. Sharpe above the baseline's in period B.
2. Sharpe above the baseline's in period A.
3. Max drawdown not above the baseline's in period B.
4. Max drawdown not above the baseline's in period A.
5. With fees and slippage × 1.5 (variant and baseline), criteria 1 and 2 still hold.

T and H are judged separately; each can pass or fail on its own.

## Reported, not pass/fail

- Stationary bootstrap 90% CI of Sharpe(variant) − Sharpe(baseline) per period (mean
  block 20 days, 5000 resamples, seed 0) and the share of resamples with a difference ≤ 0.
- Neighborhood: T with ATR multiples {2, 3, 4, 5}; H with bands {1%, 2%, 3%, 5%}.
- T and H combined (3 × ATR, 2%).
- Return, CAGR, exposure and number of entries for every row.
- None of these are used to pick a different multiple or band.

## Paper instances (started before this test runs)

At the user's request (2026-10-25) both variants also run forward as paper instances
next to `forward-v9`, with the same setup: the 42 symbols in `config.SYMBOLS`, 1000 USDT
per symbol, equal weight, daily candles:

- `v9-trail-1d`: v9 `fast=1, slow=50`, trailing exit 3 × ATR.
- `v9-band-1d`: v9 `fast=1, slow=50, entry_band=0.02`.

They are created before the archive test runs, so whatever its result, their forward
data is not chosen after the fact. `forward-v9` and `v9-ivol-1d` are not changed: the
code change must leave their behavior identical, which is checked by replaying
`forward-v9`'s recorded candles with the new code before it goes live.

## Commitments

- The test runs once. The code is tested on synthetic data only; no variant is run on
  the archive data before this document is committed. The multiple (3) and band (2%) do
  not change because of the result.
- **PASS:** the variant becomes a candidate rule for v9. Its paper instance keeps running
  and is compared with `forward-v9` when the forward test is evaluated (180 days after
  2026-09-30). That comparison is descriptive: the instances share most of their
  trades and six months is short. The forward test's own rules don't change.
- **FAIL:** the variant is not used. Its paper instance is stopped and its records are
  kept; it is not restarted because of later paper results.
- If both pass, both remain candidates; a combination of the two would need its own test.

## Known limits

- Daily closes only: the trailing level is checked on the close, so a candle can fall far
  below it during the day before the position is sold at the next open. This matches how
  the bot trades today (no exchange-side stop orders).
- A 3 × ATR(14) chandelier and a 2% band are common textbook values, not values fitted
  here; but they are not the only reasonable ones, which is why the neighborhood is
  reported.
- The two variants are two more trials on the same data. With two candidates and five
  criteria each, a pass by luck is less likely than with one criterion, but not
  ruled out.
