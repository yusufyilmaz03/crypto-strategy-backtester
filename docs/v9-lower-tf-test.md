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
