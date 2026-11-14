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

- **Done 2026-11-14 — v11 dry run report.** Four days (2026-11-10 to 11-14, 11-13 missed):
  groups of five, 20 symbols, written into `docs/v11-ai-analyst.md`; `V11_DRY_RUN = False`.

- **v11 start (after the dry-run report and the move to a server).** Fix `docs/v11-ai-analyst.md`
  as the pre-registration (commit) first. Right after a daily close, in the panel's New instance
  form: "Today's top 20" for the universe, then create the four v11 instances (strategy v11:
  astra high, astra medium, sol high, astra high with news off; group as the dry run chose) and
  `v9-v11-symbols` (v9 on the same saved universe), all started. The same from the command line:
  `universe --n 20 --save`, then `v11-setup --universe results/universe_<date>.json --group N --enable`.

## Server (next topic)

- **Memory per instance: done 2026-11-12.** Market data comes from `core/binance_public.py`
  (public REST, only the instance's own symbols) instead of ccxt, which loaded all 4,714
  markets. Physical footprint per paper instance 234 MB → about 50 MB (peak 381 → 51 MB); all
  18 processes about 1 GB (dashboard 109 MB). Only testnet instances still import ccxt
  (`trading/broker.py`, order code). 4 GB is enough.
- **Home server instead of renting (2026-11-12):** the user's HP EliteBook 840 G2 (8 GB RAM,
  SSD, Ubuntu installed). Its battery bridges short power cuts. Open: `service install` is
  macOS only (`tools/service.py`) and the Docker image has no Node/Codex CLI, so v11 can't run
  in it; plan is a systemd variant of `service install` with a native venv. Then: Node 22 via
  nvm + `codex@0.166.0` + `codex login`, copy `runtime/`, `data/`, `results/`, `.env` with the
  Mac service stopped (not near 00:00 UTC), lid switch ignored, BIOS power-on after AC loss,
  battery charge limit if the BIOS has one, Tailscale for the panel. Never run both machines.
- **Providers.** Hetzner's cost-optimized plans (CX23: 2 vCPU, 4 GB, €5.99) fit; regular
  performance is about three times the price. Compare other providers in the EU (Binance blocks
  US addresses).

## Research (notes, not decided)

- **Done 2026-10-30, `docs/v9-filters-test.md`:** S200 and E200 PASS (trade criteria);
  X200, E + X, H + E + X FAIL (they raise the win rate by cutting winners: lower profit
  factor and expectancy in the bull period).
- **S200 + E200** (both passes together): would need its own test, on seen data.
- **Fear & Greed and BTC dominance** (idea from phBot Crypto, `docs/research/011`): inputs
  for the bankroll score and v11. Fear & Greed has free daily history since 2018-02-01
  (alternative.me), so it can be tested as a score input on past data (use the value with a
  one-day lag). Dominance history is not free (CoinGecko `/global` gives only the current
  value): start recording it daily now so it has a history by the time it is tested. Neither
  goes into a score or filter before a pre-registered test.
- **Scale-in DCA test:** draft pre-registration in `docs/dca-scale-in-test.md`, waiting for
  review.
- **Universe for new instances (idea, 2026-11-09):** a point-in-time top N by 30-day volume,
  refreshed monthly, as an instance option next to the fixed `config.SYMBOLS` list. Needs a
  rule for a held symbol that leaves the universe. Running forward tests keep their list until
  they are evaluated.
- **v11 grade stakes as an instance option (deferred 2026-11-10):** with the bankroll on, the
  stake follows the model's grade. Needs a cash pool shared by the symbols (the order simulator
  keeps cash per symbol) and a way to show the pool in reports and the panel. After the bankroll
  review; the v11 test instances don't use it.
- **Scale-in DCA as an instance option (user idea, 2026-11-09):** only after the scale-in DCA
  test has run. The engine needs several fills per position and an average cost.
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
- **Where the bankroll replay lives (user, 2026-11-10).** It sits at the bottom of a bankroll
  instance's Bankroll tab for now. It may move elsewhere later (e.g. its own page or the
  Backtests page); keep it as it is until that is decided.

## Bankroll (paper, 2026-11-01)

- **Still open: the panel at phone width** (user check, not urgent): the Bankroll section of
  the New instance modal, the bankroll card, the Bankroll tab (chart, ledger, requests,
  replay) and the Deposit / Withdraw dialogs.

`phase-4g-bankroll` was merged into dev on 2026-11-10 (with 4h and 4i, fast-forward) after
the checks below; only the phone-width check was left, by the user's choice. The checks ran
on the paper instance `bankroll-v4-5m` (started 2026-11-02): v4 on 5m, 41 symbols, 10,000 USDT, stakes
2/5/7/10%, min entry 10, rotation on (profit only, reduce allowed), low bankroll below 50%
of value. v4 on 5m gives about 18 BUY signals an hour and up to 40 on one candle, so the
cash runs short quickly. `bankroll-v4-4h` (started 2026-11-02) has the same setup on 4h
plus the lower_tf score input and the lower-timeframe exit; v4 on 4h gives about 9 BUYs a
day, up to 33 on one candle. (The first two instances, `withBankroll` and `withBankroll-copy`,
ran v2 40/60/3, which gave no BUY in the last 1000 candles of any symbol on 4h or 5m; they
were deleted on 2026-11-02, their single INITIAL rows stay in paper.db.)

- **Stakes and scores.** The first BUYs write a `RESERVE` row (at the open a small `RELEASE`
  returns what lot rounding left over; there is no `BUY` ledger row) with a score and
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
  An empty result is a pass. With rotation, free can be negative on the rows of a candle that
  funded a Very strong entry against the cash a rotation will bring at the next open.
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
- **Status 2026-11-10.** Passed on the paper instances: books add up, restarts, busy candles
  (cut / no cash), the lower-timeframe exit, deposit and withdraw (5,000 in and 7,000 out on
  `bankroll-v4-5m`, applied within about 15 s, TWR without a jump; the refusal above the free
  cash is covered by tests), the low-bankroll warning (threshold 99%: badge and Telegram) and the
  Telegram views. Still open: the panel at phone width. The replay is now in the panel
  (Bankroll tab, "Replay on past data") and `bankroll-replay`.
  Strong / Very strong stakes and rotation never occur with v4: it buys below the lower Bollinger
  band, where most score inputs (trend, distance, breakout, RSI 50-70) give no points, so its BUY
  scores stay below about 56 (replays of both instances' exact settings on cached candles: max
  55.9 on 4h, 2026-04 to 2026-09; max 50.1 over 34,113 BUYs on 5m, 2026-07 to 2026-09). A replay of the same bankroll settings with v9 on cached 4h
  candles (2026-04 to 2026-09, the live BankrollRunner with faked clock and fetches) gave 119
  Very strong and 485 Strong entries, 41 rotations for 27 entries (26 TRIMs). Every rule held:
  margin of 20 points, held 3 candles or more, profit only, at most 2 per candle, filled at the
  next open, stakes 200 / 500 / 700 / 1,000, higher scores funded first. Free cash is negative
  only at the end of a candle that funded an entry against rotation proceeds (by design); the
  three shortfalls were settled as "stake cut" (cents).
- **Afterwards:** bankroll instances are untested variants. Compare them descriptively,
  with the trade metrics, before trusting any stake grades or rotation. A pre-registered
  test needs a portfolio backtest built on `core/bankroll.py`.

## Distribution (ideas, not started)

- **Public repo for the agent skill (maybe later).** A separate public repo, e.g.
  `crypto-strategy-backtester-skill`, holding the built skill (`skills/crypto-strategy-backtester/`
  plus `.claude-plugin/marketplace.json` with the repo root as the plugin). Why: Agensi is the only
  skill marketplace that sells; skills.sh, SkillsMP (crawls repos with 2+ stars), ClawHub and Claude
  Code plugin marketplaces are free and read a public GitHub repo with a SKILL.md, and
  `agent-skill/package/` alone is not installable because `build.py` copies the engine in. One repo
  would feed all four. Cost: an export option in `build.py` and a rebuild/push whenever the engine
  changes. Before ClawHub, check that it accepts a PolyForm Noncommercial skill. Paused on
  2026-11-14 to focus on showcasing the main project; details in
  `docs/showcase-and-distribution.md` ("Update 2026-11-14").

## Maintenance

- `claude/proje-baglami.md`, known issues #11 (CSV logs) is out of date: paper uses SQLite.
