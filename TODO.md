# TODO

Open work that isn't code yet. Dated items first; check this file at the start of a session.

## Calendar

- **~2026-11-30 — first look at the shadow stop data.** Instances with a stop
  (`v2-test-1d`, `v4-1d-tight`, `v4-1d-t-btc-eth`) record every stop hit in `stop_events`
  (paper.db). Compare the estimated fill (1m open vs stop) with the engine's fill at the
  stop: how optimistic is "filled at the stop price"? Input for exchange-side stops.
  Only worth reading once there are a few dozen events.
- **2027-03-29 — forward test evaluation** (180 days after 2026-09-30, criteria in
  `docs/forward-test.md`). Run `python -m kripto_bot forward-report` for `forward-v9`.
  Then compare descriptively with the variants that run next to it since before their
  tests: `v9-trail-1d` (T), `v9-band-1d` (H), `v9-ltf-exit-1d` (X, started 2026-10-30),
  `v9-ivol-1d` (inverse-volatility sizing), `v9-s200-1d` (S200) and `v9-e200-1d` (E200,
  both passed `docs/v9-filters-test.md`). Report the number of closed trades, win rate,
  profit factor and expectancy too, not only returns.

## Research (notes, not decided)

- **Done 2026-10-30, `docs/v9-filters-test.md`:** S200 and E200 PASS (trade criteria);
  X200, E + X, H + E + X FAIL (they raise the win rate by cutting winners: lower profit
  factor and expectancy in the bull period).
- **S200 + E200** (both passes together): would need its own test, on seen data.
- **Trade-level metrics in every test from now on:** closed trades, win rate, average
  win and loss, profit factor, expectancy per trade (`analysis/filters.py`).

## Needs approval first (order-sending code)

- **Testnet: retry failed updates soon.** Paper retries since 2e2ccdc; testnet still
  waits for the next close and never trades a missed candle.
- **Exchange-side stop / OCO orders** before going live (testnet refuses instances with
  a stop today).
- **Bankroll on testnet**: the steps are listed in `docs/bankroll-management.md`
  ("To-do for testnet"). Paper only until the paper bankroll instances have been reviewed.

## Telegram and panel (ideas, not started)

- **Faster manual close.** A close asked on the panel or in Telegram is sold within about
  a minute: the instance's ManualClose looks once a minute and the notifier reports
  every 30 s. Checking every few seconds would make it near instant.
- **`/kill` command in Telegram** (off / block entries / flatten, with a confirmation like
  Close). Flatten closes testnet positions, so ask for approval before writing it.
- **Charts in Telegram.** The daily summary is text only: add a PNG of each instance's
  equity vs buy-and-hold (matplotlib, drawn locally, sent with `sendPhoto`). A `/chart
  SYMBOL` command could reuse it: candles with the instance's entries and exits, the open
  position's stop and the strategy's indicator.

## Bankroll (paper, 2026-11-01) — check before merging `phase-4g-bankroll`

The branch is pushed, not merged. Merge into dev once the checks below pass on the paper
instance `bankroll-v4-5m` (started 2026-11-02): v4 on 5m, 41 symbols, 10,000 USDT, stakes
2/5/7/10%, min entry 10, rotation on (profit only, reduce allowed), low bankroll below 50%
of value. v4 on 5m gives about 18 BUY signals an hour and up to 40 on one candle, so the
cash runs short quickly. `bankroll-v4-4h` (started 2026-11-02) has the same setup on 4h
plus the lower_tf score input and the lower-timeframe exit; v4 on 4h gives about 9 BUYs a
day, up to 33 on one candle. (The first two instances, `withBankroll` and `withBankroll-copy`,
ran v2 40/60/3, which gave no BUY in the last 1000 candles of any symbol on 4h or 5m; they
were deleted on 2026-11-02, their single INITIAL rows stay in paper.db.)

- **Stakes and scores.** The first BUYs write `RESERVE` then `BUY` rows with a score and
  grade. The stake is the grade's % of the contributed capital: 200 / 500 / 700 / 1,000
  USDT for Weak / Normal / Strong / Very strong. Weak trades here, since its stake is 2%.
  The Score column is filled on Signals, Positions (with Now) and Trades.
- **Books add up.** In every ledger row free + reserved + invested equals value, and
  free is never negative:
  ```
  sqlite3 runtime/paper.db "SELECT strategy, time, kind, free_after, reserved_after,
    invested_after, value_after FROM bankroll_ledger WHERE strategy LIKE
    'instance:bankroll-v4-%' AND (free_after < -1e-6 OR abs(free_after +
    reserved_after + invested_after - value_after) > 0.01)"
  ```
  An empty result is a pass.
- **A busy candle.** When many BUYs arrive together, the higher scores are funded first.
  Once the cash runs out, rows say "cut to the free cash" or "no free cash" (`SKIP`).
- **Rotation.** A Very strong signal (score 80 or more) short of cash spends the free
  cash first. The ledger note reads "… from free cash + … expected from rotating out …".
  Only positions in profit, held 3 candles or more and scoring 20 points lower are sold.
  At most 2 are sold per candle. A reduce shows as a `TRIM` row and a REDUCE trade. The
  rotated-out symbol waits for re-entry as after a trailing exit.
- **Lower-timeframe exit with a bankroll** (`bankroll-v4-4h` only, no test covers it). A position
  sold during the candle returns its cash at once: a `SELL` row and higher free cash.
- **Deposit and withdraw** on the instance page. The request is applied within a minute
  as a `DEPOSIT` or `WITHDRAW` row. The contributed capital changes and the stakes follow
  it. The return curve (TWR) doesn't jump. A withdrawal above the free cash is refused.
- **Low-bankroll warning.** It won't trigger at 50% on its own soon. To see it, set the
  threshold on the Bankroll tab to free cash, 101%, for a candle or two. Expect the badge
  on the overview, instances and instance pages, a Telegram message and the instance in
  `/status`. Set it back: "recovered" comes once the level is 5 points above the threshold.
- **Restart.** After `service restart` both instances resume from paper.db: same free
  cash and positions, no second `INITIAL` row.
- **Telegram.** The instance view shows the bankroll table, and positions and signals
  have the Sc column. The action words "no cash", "rotate" and "reduce" appear in place.
- **Panel in a browser**, also at phone width: the Bankroll section of the New instance
  modal, the bankroll card, the Bankroll tab (chart, ledger, requests) and Deposit /
  Withdraw.
- **Afterwards:** bankroll instances are untested variants. Compare them descriptively,
  with the trade metrics, before trusting any stake grades or rotation. A pre-registered
  test needs a portfolio backtest built on `core/bankroll.py`.

## Maintenance

- `claude/proje-baglami.md`, known issues #11 (CSV logs) is out of date: paper uses SQLite.
