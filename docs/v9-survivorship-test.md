# v9 survivorship-free test (written 2026-10-12, before running it)

## Why

Every test so far used the 42 coins that trade on Binance today. Coins that were
delisted (LUNA, FTT, SRM, many 2018-era alts) are missing, and those are exactly the
crashes a trend filter claims to avoid. Leaving them out biases results in an unknown
direction: it flatters buy-and-hold (no −99% coins) and may flatter or hurt v9.
This test also gives v9 a period it has never seen: 2018–2022.

## Fixed design

**Strategy:** v9, `fast=1, slow=50`, no stop, long-only, signal on the close, fill at
the next open, fees 0.10% + slippage 5 bps per fill (`config.py`). No parameter selection.

**Data:** daily candles of every USDT spot pair in the Binance public archive
(data.binance.vision), listed or delisted, finished months up to 2026-09. Where a
pair has more than 5 days without candles, its history is split and each piece is a
separate asset (Binance reused tickers, e.g. LUNAUSDT holds the old and the new LUNA).
Excluded before the test: stablecoins, fiat and gold tokens (`STABLE_BASES` in
`kripto_bot/analysis/survivorship.py`), leveraged tokens (`…UP`, `…DOWN`, `…BULL`,
`…BEAR` whose remainder is itself a listed coin, plus the 2020 `BULL`/`BEAR` BTC
tokens) and tokenized stocks and ETFs (tickers ending in `B` first listed on or after
2026-06-01, e.g. `AAPLB`, `NVDAB`, `SPYB`; they only touch the last months of period A).
The exclusion list was completed by checking the data, not results: every asset whose
price barely moves (coefficient of variation below 2%) and every recent ticker ending
in `B` was looked at. 756 pairs → 692 assets (9 later listings of reused tickers).

**Universe (point in time):** at the start of each month, the 50 assets with the
highest USDT volume over the previous 30 days, among assets with at least 60 daily
candles and a candle on the previous day. Only data up to the previous day's close is
used.

**Portfolio:** equal weight across the month's universe, no rebalancing inside the
month. Each slot follows its asset's v9 equity (v9 runs on the asset's whole history,
so its state on joining is what it would have been). Costs beyond the engine's own
fills: one fill when an asset joins while v9 holds it, one when it leaves the
universe or is delisted while held. A delisted asset is sold at its last close and
the slot stays in cash until the month ends. Rebalancing between continuing members
is not charged (same for both sides).

**Benchmark:** equal-weight buy-and-hold of the same universe under the same rules
(it always holds, so it pays a fill whenever an asset joins or leaves).

**Periods:**
- **B, 2018-01 → 2022-09 (57 months): the primary test.** No strategy of this
  project has been run on this period.
- **A, 2022-10 → 2026-09 (48 months):** the Phase 2b development period plus the spent
  1d holdout. Not a new test; used only to measure the survivorship bias, by running
  the same machinery on the 42 survivors (`config.SYMBOLS`, each joining when it has 60
  candles).

**Metrics:** from the daily portfolio value: total return, CAGR, annualized Sharpe
(√365), max drawdown, average exposure, number of v9 entries.

## Pass criteria (period B, top 50, all must hold)

1. Sharpe of v9 above buy-and-hold's.
2. Max drawdown of v9 below buy-and-hold's.
3. Total return of v9 above zero.
4. With fees and slippage × 1.5 (both sides), criterion 1 still holds.

## Reported, not pass/fail

- 90% confidence interval of Sharpe(v9) − Sharpe(buy-and-hold) in period B from a
  stationary block bootstrap of the paired daily returns (mean block 20 days, 5000
  resamples, seed 0), and the share of resamples with a difference ≤ 0.
- The same design with a top-20 universe.
- **Neighborhood (robustness):** period B, top 50, `slow` ∈ {20, 30, 40, 50, 70, 100,
  150, 200}. SMA50 counts as "not an isolated optimum" if at least 6 of the 8 lengths
  beat buy-and-hold on both Sharpe and max drawdown. These results are never used to
  pick a new length.
- Period A: point-in-time top 50 vs the 42 survivors, for v9 and buy-and-hold.

## Commitments

- The test runs once. The code was tested on synthetic data only; v9 results on the
  archive data were not looked at before this document was committed.
- v9's parameters do not change because of the result.
- PASS strengthens v9 as the Phase 5 candidate together with the paper forward test.
  FAIL means v9's edge is not supported outside the period it was found in; the
  forward test continues but Phase 5 with v9 is not recommended without new evidence.

## Known limits

- Trend following on crypto in 2018–2022 is discussed in public research, and the
  broad shape of that market (2018 and 2022 crashes, 2020–21 boom) is common
  knowledge. Period B is new data for this project's choices (SMA50 was picked on
  2022–2026), not a test of an idea nobody has seen.
- The archive starts in 2017; early 2018 has few USDT pairs, so the universe is
  smaller than 50 at first: 2 assets in January 2018, 38 on average over period B
  (period A always has 50).
- Daily candles: intraday moves and the exact delisting price are not modeled.
