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
  `v9-ivol-1d` (inverse-volatility sizing). Report the number of closed trades and
  winners/losers too, not only returns.

## Research (notes, not decided)

- **SMA trend filter at entry** (a variable SMA length per instance). Aim: more reliable
  entries, not more return. Needs its own pre-registration with one fixed length.
- **Combinations** H + E + X, E + X: the best Sharpe and drawdown in
  `docs/v9-lower-tf-test.md`, but only reported there. Using one needs its own test, on
  data that has been seen.
- **Trade-level reliability metrics** in every test: closed trades, winners vs losers
  (win rate), average win and loss, profit factor, expectancy per trade. The filters aim
  at fewer losing trades, which the portfolio metrics (return, Sharpe, drawdown) show
  only indirectly.

## Needs approval first (order-sending code)

- **Testnet: retry failed updates soon.** Paper retries since 2e2ccdc; testnet still
  waits for the next close and never trades a missed candle.
- **Exchange-side stop / OCO orders** before going live (testnet refuses instances with
  a stop today).

## Maintenance

- Merge `phase-4c-readiness` and `phase-4d-intrabar` into `dev` / `main` when wanted.
- `phase-4e-telegram` was opened from `phase-4d-intrabar` while 4d still got commits;
  bring it up to date with 4d before merging.
- `claude/proje-baglami.md`, known issues #11 (CSV logs) is out of date: paper uses SQLite.
