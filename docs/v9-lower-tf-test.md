# v9 lower-timeframe rules test (written 2026-10-30, before running it)

## Why

A daily instance looks at the market once a day. Two rules that use the 4h and 1h
candles in between were built as instance settings (`docs/intrabar-watch.md`):

- **E, entry confirmation:** a daily BUY enters only if the 4h and 1h candles agree.
  It targets the same whipsaw entries as variant H (entry band 2%, passed in
  `docs/v9-exit-test.md`).
- **X, early exit:** a position is sold inside the day as soon as the 4h and 1h
  candles both say SELL. It targets the same late exits as variant T (trailing exit
  3 × ATR, passed in the same test).

This test asks whether either rule improves v9 and whether it does better than the
simpler variant that already targets the same problem.

## Status of the data (read this first)

No unseen daily data is left: the 1d holdout is spent and both periods below have
been used for v9 (survivorship, sizing and exit tests). The 4h and 1h candles are new
to these tests, but the days they belong to are not. This is a new trial on data we
have seen, so its result is supporting evidence, not an independent test. The forward
data that motivated the rules (2026-09-30 on) is in neither period.

## Fixed design

Everything as in `docs/v9-exit-test.md` and `docs/v9-survivorship-test.md`:
point-in-time top-50 universe, equal weight, periods B (2018-01 → 2022-09) and A
(2022-10 → 2026-09), signal on the daily close, fill at the next open, fees 0.10% +
slippage 5 bps per fill, the same join, leave and delisting costs.

**Lower-timeframe data:** 4h and 1h candles of every asset in either universe from
the Binance public archive (data.binance.vision), full history, finished months up to
2026-09. Where a ticker's daily history is split into several assets (a reused ticker),
each asset gets the lower-timeframe candles of its own days.

**The rules are the code the paper bot runs** (`core/lower_timeframes.py`,
`Engine.on_bar(entry_ok=...)`, `Engine.exit_inside`), called by the backtest exactly as
`SymbolRunner.step` calls them. Lower-timeframe signals are v9 `fast=1, slow=50` on
the 4h and 1h candles themselves.

- **Baseline:** v9 `fast=1, slow=50`, no stop.
- **E:** the baseline, plus: at a daily close with a BUY signal, the entry is queued
  only if the last 4h candle and the last 1h candle that closed by that time (the ones
  ending at the daily close) both have a v9 BUY signal. Otherwise no entry; the next
  daily close decides again. A missing lower candle means no entry.
- **X:** the baseline, plus: in a day in which v9 holds a long (or its entry fills at
  that day's open) and no exit is queued for the open, after each 1h close strictly
  inside the day (01:00 … 23:00 UTC), if the last closed 4h candle and the last closed
  1h candle both have a v9 SELL signal, the position is sold at the open of the next 1h
  candle, with the usual fee and slippage. A missing lower candle means no decision at
  that hour. After an X exit the next entry needs a daily candle whose v9 signal was not
  BUY, or a daily close above the exited trade's highest daily close (its entry price
  if it had none): the re-entry rule of variant T. (A stop reached before the exit
  would win, but the baseline has no stop.)
- **Comparators, run again in the same run:** H (entry band 2%) for E, T (trailing exit
  3 × ATR) for X.

## Pass criteria (per variant, all must hold)

1. Sharpe above the baseline's in period B.
2. Sharpe above the baseline's in period A.
3. Max drawdown not above the baseline's in period B.
4. Max drawdown not above the baseline's in period A.
5. With fees and slippage × 1.5 (variant and baseline), criteria 1 and 2 still hold.

E and X are judged separately; each can pass or fail on its own.

**Against the comparator:** a variant that passes is *preferred* over its comparator
(E over H, X over T) only if its Sharpe is at least the comparator's in both periods.
Otherwise the comparator stays the candidate for that problem: it is simpler and needs
no lower-timeframe data.

## Validity check (before any result is read)

The baseline, H and T rows must reproduce `results/exit_results.csv` (same data, same
code path). If they don't, the run is not read and the difference is found first.

## Reported, not pass/fail

- Stationary bootstrap 90% CI of Sharpe(variant) − Sharpe(baseline) and of
  Sharpe(variant) − Sharpe(comparator), per period (mean block 20 days, 5000 resamples,
  seed 0), and the share of resamples with a difference ≤ 0.
- Single-timeframe neighbors: E and X with only 4h and with only 1h (X with only 4h
  decides at 4h closes and sells at the next 4h open).
- E and X combined, and the combinations the paper instances run: H + E + X and
  T + E + X.
- Return, CAGR, exposure, number of entries and number of X exits for every row.
- None of these are used to pick a different variant.

## Paper instances

The user created two paper instances before this document: `v9-band-1d-ltf-cls`
(H + E + X) and `v9-trail-1d-ltf-cls` (T + E + X), both with manual close on. They run
combinations, not E or X alone, and a manual close changes their results, so they are
not part of this test's forward comparison. Before the test runs, two paper instances
with the setup of `forward-v9` plus one rule each, manual close off, are created so
their forward data is not chosen after the fact: `v9-ltf-entry-1d` (E) and
`v9-ltf-exit-1d` (X).

## Commitments

- The test runs once. The code is tested on synthetic data only; no variant is run on
  the archive data before this document is committed. The rules, timeframes and
  criteria don't change because of the result.
- **PASS:** the variant becomes a candidate rule for v9 (preferred over its comparator
  or not, as above). Its paper instance keeps running and is compared with `forward-v9`
  when the forward test is evaluated (180 days after 2026-09-30); that comparison is
  descriptive.
- **FAIL:** the variant is not used. Its paper instance is stopped and its records are
  kept; it is not restarted because of later paper results.

## Data note (2026-10-30, after the download, before the test ran)

The 4h and 1h data of all 391 symbols downloaded. After Binance's outage of
2018-02-08 the archive's candles opened at hh:28:14.789 instead of on the hour for
about two days (2018-02-09 to 2018-02-11): 258 hourly candles in 6 files and 18
four-hour candles in 2 files. They are kept as they are. The rules look up candles at
the hour (or 4h) boundary, so in those hours they find no candle and take no decision,
as for any missing candle. Reading the cache now parses timestamps as ISO8601 (the
milliseconds made pandas leave a whole column as text); the 1d files read exactly as
before.

## Known limits

- Decisions are taken at 1h closes; the paper bot takes them a few minutes after each
  1h close and fills at the open of the 1m candle starting at that time, which is the
  same first trade as the 1h candle's open. Slippage beyond 5 bps on a fast hour is not
  modeled.
- v9 on 1h candles compares the close with the average of the last 50 hours, about two
  days: it says SELL often, even in a daily uptrend. That noise is what the test
  measures; requiring both 4h and 1h is the only filter against it, fixed in advance.
- Two primary variants on data that has been used: with five criteria each a pass by
  luck is less likely than with one, but not ruled out. The neighbors and combinations
  are more trials and are reported only.

## Result (run once, 2026-10-30)

396 assets were in a universe at some point. Output: `results/lower_tf_results.csv`.
**Validity check passed:** the baseline, H and T rows reproduce `results/exit_results.csv`.
The paper instances `v9-ltf-entry-1d` and `v9-ltf-exit-1d` had been started before the
run (2026-10-30, first daily candle 2026-10-30).

| | B baseline | B: E | B: X | B: H | B: T | A baseline | A: E | A: X | A: H | A: T |
|---|---|---|---|---|---|---|---|---|---|---|
| Return | +676.0% | +833.7% | +376.1% | +768.0% | +713.4% | −9.2% | −3.6% | +27.4% | −5.3% | +0.5% |
| CAGR | 53.9% | 60.0% | 38.9% | 57.6% | 55.5% | −2.4% | −0.9% | 6.2% | −1.3% | 0.1% |
| Sharpe | 1.09 | **1.17** | **1.24** | 1.14 | 1.14 | 0.14 | **0.17** | **0.39** | 0.16 | 0.19 |
| Max drawdown | 55.1% | **51.8%** | **35.7%** | 50.6% | 52.4% | 64.0% | 64.1% ❌ | **32.6%** | 63.3% | 60.0% |
| Exposure | 43.0% | 41.6% | 18.0% | 41.3% | 39.7% | 41.2% | 40.1% | 16.7% | 38.9% | 37.2% |
| Entries | 1954 | 1679 | 3200 | 1489 | 1982 | 2242 | 1964 | 3437 | 1611 | 2280 |
| X exits | – | – | 2799 | – | – | – | – | 2970 | – | – |
| Sharpe, costs × 1.5 | 1.05 | **1.14** | **1.16** | 1.12 | 1.11 | 0.09 | **0.13** | **0.27** | 0.12 | 0.14 |

- **E: FAIL.** Five of six criteria hold; the max drawdown in period A is 64.07%
  against the baseline's 63.98%, 0.09 points above it. Bootstrap 90% CI of
  Sharpe(E) − Sharpe(baseline): B [0.04, 0.13] (P(diff ≤ 0) = 0.0%), A [−0.01, 0.08]
  (8.5%). Against H: B [−0.03, 0.08] (21.7%), A [−0.03, 0.05] (28.0%), so E would not
  have been preferred over H either.
- **X: PASS, preferred over T** (Sharpe 1.24 vs 1.14 in B, 0.39 vs 0.19 in A). Bootstrap
  90% CI of Sharpe(X) − Sharpe(baseline): B [−0.24, 0.57] (P(diff ≤ 0) = 27.4%), A
  [−0.17, 0.67] (16.5%). Against T: B [−0.28, 0.48] (34.5%), A [−0.20, 0.60] (20.6%).
- Neighbors, Sharpe B / A (baseline 1.09 / 0.14): E 4h only 1.17 / 0.16, E 1h only
  1.09 / 0.15; X 4h only 1.26 / 0.40, X 1h only −0.27 / −0.29 (5,600 exits per period:
  hourly v9 alone sells almost every trade at once).
- Combinations, descriptive: E + X 1.37 / 0.47 (max DD 31.2% / 32.0%); H + E + X 1.39 /
  0.57 (28.1% / 29.6%); T + E + X 1.37 / 0.48 (in B identical to E + X: X always sells
  before the trailing exit would).
- With costs × 1.5, X makes 3 fewer exits: after an X exit of a trade with no daily close
  yet, the re-entry level is its entry fill price, which includes slippage.

Interpretation:

- **E** is a small, consistent refinement in period B (its bootstrap interval is above
  zero there) and close to nothing in A, where it fails the drawdown criterion by a
  hair. It does about what H does: not distinguishable from it in either period. Per the
  commitments it is not used, and the fail is not argued away: it was the rule fixed in
  advance.
- **X** is a different strategy profile, not a refinement. It sells most trades within
  hours (2,800–3,000 X exits; exposure falls from about 42% to 17%), which cuts the
  drawdown roughly in half in both periods and raises Sharpe by the point estimate. The
  price is absolute return in the bull period: +376% instead of +676% in B. In the
  sideways-to-down period A it turns −9% into +27%. The Sharpe gain is not established:
  the bootstrap intervals are wide and include zero (P(diff ≤ 0) 17–27%), unlike the
  drawdown cut, which is large in both periods. Most of the effect comes from the 4h
  candles (X 4h only ≈ X); 1h alone is noise, as expected.
- Both periods had been seen before, so this is supporting evidence. Per the commitments:
  `v9-ltf-entry-1d` (E) is stopped and its records kept; `v9-ltf-exit-1d` (X) keeps running
  and is compared with `forward-v9` at its 180-day evaluation. The combinations would
  need their own test.
