# v9 reliability filters test (written 2026-10-30, before running it)

## Why

The filters built so far are meant to make v9's trades more reliable, not to make
more money: enter only when the trend is confirmed, and get out before a trade turns
into a loss. Such a filter can lower the total return and still be what the user
wants. The previous tests judged variants by Sharpe and drawdown; this one judges them
by their trades: how many win, how much a winner brings against a loser, and what a
trade is worth on average.

Two new instance settings are tested next to the combinations that the lower-timeframe
test (`docs/v9-lower-tf-test.md`) could only report:

- **Trend filter (`trend_sma`):** a BUY at the daily close enters only if the close is
  above SMA(N) of the daily closes.
- **SMA mode of the lower-timeframe rules (`lower_tf_sma`):** rules E and X read "close
  above / below SMA(N)" on the 4h and 1h candles instead of the strategy's own signal on
  them.

## Status of the data (read this first)

No unseen daily, 4h or 1h data is left: all of it was used by the survivorship, sizing,
exit and lower-timeframe tests, and the combinations below were chosen after seeing
their reported results in the last one. This is a further trial on seen data, with five
primary variants: its result is supporting evidence, and a pass by luck is possible.
The forward data (2026-09-30 on) is in neither period.

## Fixed design

Everything as in `docs/v9-lower-tf-test.md`: point-in-time top-50 universe, equal
weight, periods B (2018-01 → 2022-09) and A (2022-10 → 2026-09), signal on the daily
close, fill at the next open, fees 0.10% + slippage 5 bps per fill, the same join,
leave and delisting costs, the same 4h and 1h archive data, the paper bot's decision
code (`core/lower_timeframes.py`). Baseline: v9 `fast=1, slow=50`, no stop.

**New rules, as the paper bot will run them:**

- **Trend filter, length N:** at a daily close with a BUY signal the entry is queued only
  if that close is above the mean of the last N daily closes (that close included). With
  fewer than N closes there is no entry. Exits are not affected.
- **Lower-timeframe SMA mode, length N:** for E and X, each lower timeframe's signal is
  BUY when its close is above the mean of its last N closes, SELL when below, nothing
  during the first N − 1 candles. Everything else about E and X stays as tested.
  With N = 50 this is exactly v9's own signal (`fast=1, slow=50`), so E and X with
  SMA mode 50 must reproduce the E and X rows (validity check).

**Primary variants (each judged on its own):**

| Name | Rule(s) |
|---|---|
| S200 | trend filter, N = 200 |
| E200 | E in SMA mode, N = 200 on 4h and 1h |
| X200 | X in SMA mode, N = 200 on 4h and 1h |
| E+X | E and X with v9's signal on 4h and 1h (as tested before) |
| H+E+X | entry band 2% (variant H) plus E and X; lower timeframes use the same v9 parameters, band included, as the paper bot does |

## Trade metrics

Computed over the closed trades of every asset whose entry falls in a month in which the
asset is a member of that period's universe and whose exit is before the period ends
(trades closed at the end of the data are left out). A trade's return is the engine's
`return_pct`: after fees and slippage, relative to the sleeve's equity at entry, so every
trade counts the same whatever its asset's size.

- **Win rate:** share of trades with a return above zero.
- **Profit factor:** sum of the positive returns / |sum of the negative returns|.
- **Expectancy:** mean return per trade.

## Pass criteria (per primary variant, against the baseline, all must hold)

1. Win rate higher, in period B and in period A.
2. Profit factor not lower, in B and in A.
3. Expectancy not lower, in B and in A.
4. Max drawdown of the portfolio not higher, in B and in A.
5. With fees and slippage × 1.5 (variant and baseline), criteria 1 to 3 still hold in B
   and in A.

Return, CAGR and Sharpe are reported and not judged: a filter may give up return.

## Validity checks (before any result is read)

1. The baseline, H, T, E and X rows reproduce their rows in
   `results/lower_tf_results.csv` (return, Sharpe, max drawdown, entries).
2. E and X in SMA mode with N = 50 reproduce the E and X rows exactly.

If either fails, the run is not read and the difference is found first.

## Reported, not pass/fail

- Every metric for every row, including the reference rows: baseline, H, T, E and X.
- Neighbors: trend filter N = 100, 150, 250; E and X in SMA mode with N = 100.
- S200 combined with X: S200 + X.
- Stationary bootstrap 90% CI of Sharpe(variant) − Sharpe(baseline) per period, as in
  the previous tests (daily portfolio returns; mean block 20 days, 5000 resamples,
  seed 0).
- None of these are used to pick a different variant or length.

## Paper instances

Before the test runs, one paper instance per primary variant is created with the
setup of `forward-v9` plus its rule(s), manual close off, so its forward data is not
chosen after the fact: `v9-s200-1d`, `v9-e200-1d`, `v9-x200-1d`, `v9-ex-1d`,
`v9-hex-1d`. (The user's `v9-band-1d-ltf-cls` runs H + E + X with manual close on and is
not part of the comparison.)

## Commitments

- The test runs once. The code is tested on synthetic data only; no variant is run on
  the archive data before this document is committed. Lengths, rules and criteria don't
  change because of the result.
- **PASS:** the variant is a candidate filter for v9. Its paper instance keeps running
  and is compared with `forward-v9` at the forward evaluation (2027-03-29, see
  `TODO.md`), by its trades as well as its returns; the comparison is descriptive.
- **FAIL:** the variant is not used; its paper instance is stopped and its records are
  kept.

## Known limits

- Five primary variants on seen data, each with ten conditions. The conditions are
  strict (all must hold in both periods), but the variants are related (four of them
  use E or X), so their results are not independent either.
- Win rate and profit factor depend on how trades are cut: a rule that sells more often
  makes more, shorter trades. The expectancy criterion is the guard against raising the
  win rate by cutting winners short.
- The trend filter and the lower-timeframe SMA mode can only block entries or sell
  earlier; neither adds trades that v9 would not have taken, except re-entries after
  an X exit.
