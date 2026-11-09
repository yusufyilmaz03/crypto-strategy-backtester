# Scale-in DCA test (draft, written 2026-11-07, before any code or result)

**Status: draft.** Once the user has reviewed the design, this document is committed as the
pre-registration before the simulator is run on archive data. After that it does not change.

## Why

Many retail bots (phBot Crypto, 3Commas-style DCA bots, Passivbot's grid) buy more as the
price falls, average down, and sell at a profit target above the average cost, usually
without a stop. They report high win rates. The risk is in the tail: a long decline or a
delisting locks or destroys the capital. Tests on today's surviving coins hide exactly
those cases, so this family is where survivorship bias should flatter results the most.
This project has a point-in-time universe that includes delisted coins
(`docs/v9-survivorship-test.md`), which is the right place to ask:

1. Does scale-in DCA beat buy-and-hold on a survivorship-free universe after costs?
2. How large is its tail: drawdown, capital locked in losing cycles, losses on delistings?
3. How much does testing on survivors flatter it?

## Fixed design

### Strategy (per asset)

One cycle at a time per asset, long-only, spot, no leverage, **no stop**.

1. **Entry order:** at a daily close, a limit buy `Initial` % below that close (market order
   at the next open if `Initial` = 0).
2. **Scale-in ladder:** after each fill, the next limit buy `Scale` % below the last fill
   price, its USDT size × `Factor` the previous fill's size. At most `Fills` fills per cycle.
3. **Take-profit:** a limit sell of the whole position `Target` % above the average fill
   price, recomputed after every fill.
4. When the take-profit fills, the cycle ends and a new entry order is placed at that
   day's close.

**Sizing:** each asset runs with its own budget. The first fill's size is chosen so that
all `Fills` fills together use exactly the budget:
`size_1 = budget × (Factor − 1) / (Factor^Fills − 1)`. A cycle's budget is the asset's value
(cash + position) when the cycle starts, so results compound.

**Configurations** (taken as published in the phBot Crypto documentation's examples, not
chosen or tuned by this project):

| Name | Initial | Scale | Factor | Fills | Target |
|---|---|---|---|---|---|
| **S, small initial (primary)** | 1% | 2% | 1.05 | 20 | 1.5% |
| C, continuous | 0% (market) | 2% | 1.05 | 20 | 1.5% |
| K, crashes | 15% | 10% | 1.05 | 20 | 1.5% |

With S, the 20th fill is about 32% below the first one.

### Fills on daily candles

Orders are placed at a daily close and are live during the next candle.

- **Limit buy at L:** fills if the candle's low goes **below** L (touching is not enough), at
  L; if the candle opens below L, at the open plus slippage. Several ladder levels can fill
  in one candle (each at its level, or the open if it gapped below it).
- **Take-profit at T:** fills if the high goes above T, at T; if the candle opens above T, at
  the open minus slippage.
- **A candle that reaches both** a ladder buy and the take-profit: the order of the moves is
  unknown, so the buy(s) fill and the take-profit does not; the new take-profit (from the new
  average) is live from the next candle.
- **Costs:** fee 0.10% on every fill (`config.py`); slippage 5 bps on market and gap fills
  only.

### Universe and portfolio

The same machinery and data as the survivorship test, unchanged:

- Daily candles of every USDT spot pair in the Binance archive, listed or delisted, the same
  exclusions and the same splitting of reused tickers.
- Point-in-time universe: at each month start, the 50 assets with the highest USDT volume
  over the previous 30 days (at least 60 candles, a candle on the previous day).
- Each asset's DCA runs on its whole history (its state on joining is what it would have
  been). The portfolio holds the month's members in equal weight, rebalanced at month
  starts. Joining or leaving costs one fill on the invested part of the sleeve.
- **Leaving the universe:** the sleeve leaves the portfolio at the month start, so its open
  position is sold then (the vendor's bot would keep holding until the target; see Known
  limits).
- **Delisting:** an open position is sold at the asset's last close; the slot stays in cash
  until the month ends.

**Benchmark:** equal-weight buy-and-hold of the same universe under the same rules (from the
survivorship test code). v9's result on the same periods is shown for reference.

**Periods:** B, 2018-01 → 2022-09 (primary); A, 2022-10 → 2026-09.

## Pass criteria (period B, top 50, configuration S, all must hold)

1. Sharpe (daily, annualized √365) above buy-and-hold's.
2. Max drawdown below buy-and-hold's.
3. Total return above zero.
4. With fees and slippage × 1.5, criterion 1 still holds.

The same criteria as the v9 survivorship test, so the two are comparable.

## Reported, not pass/fail

- Configurations C and K, period B.
- **Tail metrics** for every run: cycles completed and their win rate; average and longest
  cycle; cycles that used all fills; share of capital in cycles open longer than 90 days
  (average and peak); number of cycles ended by delisting or by leaving the universe, and
  their total loss; worst single cycle.
- Bootstrap 90% interval of Sharpe(S) − Sharpe(buy-and-hold) in period B (stationary block,
  mean block 20 days, 5000 resamples, seed 0).
- **Survivorship bias:** period A, S on the point-in-time top 50 vs on the 41 symbols the
  bot trades today (`config.SYMBOLS`), and the same comparison for buy-and-hold.

## Commitments

- The simulator is tested on synthetic data only before this document is committed. No DCA
  result on archive data is looked at before that.
- The test runs once. The configurations do not change because of the result.
- A PASS does not start a paper instance by itself: the strategy needs resting limit orders
  and has no stop, so it would need its own design and, beyond paper, explicit approval.

## Known limits

- Daily candles: the order of moves inside a day is unknown (handled by the rule above);
  no order book, queue position or partial fills.
- Leaving the universe forces a sale the vendor's bot would not make. This can cut a cycle
  at a loss or save capital from a falling coin; the number and result of these exits are
  reported.
- The vendor's bot keeps one buy order live at a time and re-places it within seconds; the
  daily model lets several ladder levels fill in one candle, which is the closest daily
  equivalent.
- Period B was the primary period of the v9 survivorship test, so it is not new to this
  project, but no DCA rule was chosen on it.

## Implementation outline

- `analysis/dca.py`: per-asset cycle simulator (orders, ladder, average, take-profit, fill
  rules, budget) producing a daily sleeve value and a cycle log; runs on the survivorship
  module's assets, universes and portfolio functions.
- `python -m kripto_bot dca-test` writes `results/dca_*.csv`.
- Tests on synthetic candles: sizes sum to the budget; strict-below limit fills and gap
  fills; several levels in one candle; buy-and-target candle rule; average and target after
  each fill; delisting sells at the last close; costs.
