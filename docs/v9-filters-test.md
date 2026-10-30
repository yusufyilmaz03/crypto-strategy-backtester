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

## Result (run once, 2026-10-30)

396 assets were in a universe at some point. Output: `results/filters_results.csv`.
**Both validity checks passed:** the baseline, H, T, E and X rows reproduce
`results/lower_tf_results.csv`, and E and X in SMA mode 50 equal E and X exactly. The five
paper instances had been started before the run (2026-10-30).

Trade metrics and portfolio results, costs as in the engine:

| Period B (2018-01 → 2022-09) | Baseline | S200 | E200 | X200 | E+X | H+E+X |
|---|---|---|---|---|---|---|
| Closed trades | 1948 | 943 | 1490 | 2426 | 2741 | 2270 |
| Win rate | 20.3% | **24.1%** | **23.3%** | **23.8%** | **30.4%** | **32.7%** |
| Profit factor | 2.90 | **3.06** | **3.33** | 2.52 ❌ | 1.83 ❌ | 1.87 ❌ |
| Expectancy per trade | +10.05% | **+12.37%** | **+13.65%** | +6.43% ❌ | +3.40% ❌ | +3.96% ❌ |
| Average win / loss | +75.7% / −6.6% | +76.3% / −7.9% | +83.8% / −7.6% | +44.8% / −5.6% | +24.7% / −5.9% | +26.0% / −6.8% |
| Max drawdown | 55.1% | **41.3%** | **51.7%** | **50.5%** | **31.2%** | **28.1%** |
| Return | +676% | +294% | +901% | +648% | +459% | +449% |
| Sharpe | 1.09 | 0.97 | 1.20 | 1.18 | 1.37 | 1.39 |
| Exposure | 43.0% | 23.6% | 40.9% | 32.1% | 17.7% | 16.7% |

| Period A (2022-10 → 2026-09) | Baseline | S200 | E200 | X200 | E+X | H+E+X |
|---|---|---|---|---|---|---|
| Closed trades | 2203 | 1135 | 1713 | 2672 | 2973 | 2269 |
| Win rate | 15.7% | **18.4%** | **17.2%** | **18.9%** | **26.1%** | **28.9%** |
| Profit factor | 1.08 | **1.10** | **1.17** | **1.15** | **1.18** | **1.23** |
| Expectancy per trade | +0.39% | **+0.57%** | **+0.88%** | **+0.57%** | **+0.63%** | **+0.96%** |
| Max drawdown | 64.0% | **53.8%** | **62.6%** | **54.1%** | **32.0%** | **29.6%** |
| Return | −9.2% | +3.1% | +2.6% | +7.9% | +36.5% | +45.8% |
| Sharpe | 0.14 | 0.17 | 0.21 | 0.22 | 0.47 | 0.57 |

With costs × 1.5 every sign is the same as above (S200 and E200 keep all three trade
criteria in both periods; X200, E+X and H+E+X miss profit factor and expectancy in B).

- **S200: PASS.** All 14 conditions hold.
- **E200: PASS.** All 14 conditions hold. Bootstrap 90% CI of Sharpe(E200) −
  Sharpe(baseline): B [0.07, 0.17] (P(diff ≤ 0) = 0.0%), A [0.03, 0.12] (0.3%).
- **X200: FAIL**, **E+X: FAIL**, **H+E+X: FAIL**: each misses profit factor and
  expectancy in period B, with and without the cost stress; everything else holds.
- Sharpe CIs of the others, B / A: S200 [−0.48, 0.26] (68.5%) / [−0.29, 0.35] (41.5%);
  X200 [−0.14, 0.31] / [−0.10, 0.27]; E+X [−0.13, 0.70] / [−0.10, 0.75]; H+E+X [−0.11,
  0.72] / [−0.01, 0.85].
- Neighbors (reported only), win rate / profit factor / expectancy / Sharpe in B:
  S100 23.1% / 2.96 / +12.66% / 1.15; S150 24.3% / 2.85 / +11.59% / 0.87; S250 23.5% /
  2.98 / +11.83% / 0.97; E100 22.5% / 3.18 / +12.76% / 1.17; X100 27.5% / 2.18 / +4.77% /
  1.29. S200 + X: 30.7% / 1.88 / +3.54% / 1.14.
- Reference rows judged by the same yardstick (not part of this test's verdict): E
  21.9% / 3.16 / +11.96% in B, 16.4% / 1.12 / +0.60% in A, would pass the trade
  criteria; X 28.5% / 1.75 / +2.82% in B would not.

Interpretation:

- **Every filter raises the win rate. Only the entry filters raise it honestly.** Rules
  that sell early (X200, E+X, H+E+X, and X itself) win more often because they take
  small gains: the average winner falls from +76% to +25–45% in B, the expectancy per
  trade by a third to two thirds. v9 lives on a few large winners; cutting them makes
  the trades look more reliable and earn less each. In the sideways period A the same
  rules do improve every trade metric, so their value depends on the market regime.
- **E200 is the clearest result so far.** It improves every trade metric and the
  drawdown in both periods, keeps v9's exposure and its large winners (average winner
  +84%), and its Sharpe gain is the first in these tests whose bootstrap interval is
  above zero in both periods. Its neighbors point the same way: profit factor and
  expectancy rise from E50 (= E) to E100 to E200.
- **S200 passes, at a price.** Fewer, better trades and a much smaller drawdown, but
  it halves the exposure, cuts the return in B from +676% to +294% and lowers Sharpe
  there (0.97). Its neighbors are uneven (S150's profit factor is below the baseline's,
  S100 keeps most of the return), so the length matters more than one would like.
- Seen data and five related primaries: supporting evidence. Per the commitments:
  `v9-s200-1d` and `v9-e200-1d` keep running and are compared with `forward-v9` at the
  forward evaluation; `v9-x200-1d`, `v9-ex-1d` and `v9-hex-1d` are stopped and their
  records kept. A combination of the two passes (S200 + E200) would need its own test.
