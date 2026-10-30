# Intrabar watch and lower-timeframe rules (plan, written 2026-10-30)

Branch: `phase-4d-intrabar`. This document records the idea, the decisions and the
design before any code is written. It is not a pre-registration: the tests of the new
rules (E and X below) get their own pre-registration document before they run.

## Where the idea came from

A 1d instance only looks at the market once a day, when the daily candle closes. Two
consequences:

- **Stops.** The engine checks a stop against the daily candle's low and fills it at
  `min(open, stop)` (`core/engine.py`), as if the stop had rested on the exchange. The
  paper result is therefore already "intrabar", but it *assumes* the stop fills exactly
  at the stop price, and the bot only notices the hit at the daily close. The testnet bot
  has no stops at all (v9 doesn't use them; exchange-side stop orders were postponed in
  Phase 4).
- **Late exits and whipsaws.** In the first 25 days of `forward-v9`, 23 of 53 trades had
  been more than 3% in profit before v9's exit sold them at a loss, and 16 trades lasted
  1–3 days (`docs/v9-exit-test.md`). Lower timeframes (4h, 1h) see a turn earlier than
  the daily close.

## The three pieces

| | When it runs | Which symbols | What it looks at | Changes results? |
|---|---|---|---|---|
| **Shadow stop** | Intraday, every minute | In a position with a stop | Closed 1m candles' low vs the stop | No (observation only) |
| **E, lower-timeframe entry confirmation** | At the main candle close | Flat, main signal BUY | Strategy signal on the lower timeframes | Yes (new rule) |
| **X, lower-timeframe early exit** | Intraday, at each lower-timeframe close | In a position | Strategy signal on the lower timeframes | Yes (new rule) |

### Shadow stop

Records when and at what price a stop would really have been hit, without closing the
position. The engine still closes it at the main candle close, as today, so paper stays
identical to the backtest. Purpose:

1. Measure the stop slippage the backtest assumes away: the gap between the stop price
   and a realistic fill (fast moves fall through the stop).
2. Notify on Telegram when a stop is hit instead of at the daily close.
3. Build the intraday loop that X (and later live, exchange-side stops) will use.

Rejected alternative: let the watcher close the position itself. It would make paper
diverge from the backtest, risk processing one stop twice (watcher and engine) and break
the comparability of the forward tests.

### E: entry confirmation

When the main timeframe gives BUY and the instance is flat, check the last closed candle
of each lower timeframe. Enter only if all of them say BUY too; otherwise wait. v9's BUY
is a state (close above the SMA on every candle), not a crossover, so "wait" needs no
extra logic: if the close is still above the SMA at the next main candle, BUY comes
again and the lower timeframes are checked again.

- The daily close (00:00 UTC) is also a 4h and a 1h close, so E needs no intraday loop
  and is simple to backtest.
- Concern: at the moment the daily close crosses above the SMA50 the last hours have
  usually risen, so the lower timeframes probably agree most of the time and the filter
  may rarely bite. It targets the same whipsaw problem as variant H (2% entry band,
  passed), so it has to beat H, not just the baseline.
- Decided: when confirmation fails, wait for the next main candle (not "enter intraday as
  soon as the lower timeframes turn BUY", which would be another variant).

### X: early exit

While in a position, if the strategy says SELL on the last closed candle of **both**
lower timeframes, exit at the next lower-timeframe open instead of waiting for the main
close.

- Needs a re-entry rule, otherwise v9 buys back at the next main close (its BUY still
  holds). Use variant T's: the next entry needs a main candle whose signal was not BUY
  since the exit, or a close above the exited trade's highest close.
- Targets the same late-exit problem as variant T (3 × ATR trailing exit, passed), so it
  has to beat T, not just the baseline.
- Backtesting it needs a sub-candle simulation (lower-timeframe candles inside each main
  candle, in order, without lookahead).

## Decisions (2026-10-30, with the user)

- **Only for 4h and 1d instances.** Lower timeframes of 5m–30m are too noisy: v9 on 5m
  says SELL many times a day, which would turn the rule into a different strategy.
- **Fixed timeframe mapping:** 1d → 4h + 1h; 4h → 1h + 15m.
- **"Enough signals" = all lower timeframes agree** (both), not a vote or a count: one
  fixed definition, fewer forking paths.
- **Lower-timeframe signals use the instance's strategy and parameters unchanged**
  (v9 `fast=1, slow=50` on 4h/1h), no tuning.
- **Opt-in per instance**, chosen when an instance is created: `lower_tf_entry`,
  `lower_tf_exit` (setup fields, locked after creation like the others). Off by default,
  so existing instances don't change. The code change is checked by replaying
  `forward-v9`'s recorded candles: off must reproduce them exactly.
- **E and X are tested separately**, each against the baseline and against the existing
  variant that targets the same problem (H for E, T for X). The combination is reported,
  but adopting it would need its own test. No unseen daily data is left, so the result
  is supporting evidence, not an independent test (same caveat as `v9-exit-test.md`).
- **Shadow stop needs no setting:** it is observation only and is on for every eligible
  instance.

## Shared code

The three pieces share data access but not their trigger, so:

- **`core/lower_timeframes.py`**: closed lower-timeframe candles of a symbol up to time t
  (lookahead-safe) and the strategy's signal on them. Used by both the backtest and paper,
  like the Engine, so the two can't diverge.
- **`trading/position_watch.py`**: the intraday loop over open positions. Shadow stop now,
  X later. Each rule is a small separate function with its own tests.
- E is a hook in the main candle step, not part of the loop.

## Shadow stop design

- **Runs in each paper instance's own process.** The engine's position and stop are in
  memory there; a separate service would have to read state from the database and handle
  locks. Between main closes, the wait loop (`sleep_until_next_close` today) calls
  `position_watch` at the start of every minute while there are open positions.
- **Eligible:** paper instance, `stop != "none"`, timeframe 4h or 1d. On 2026-10-30 that is
  `v2-test-1d`, `v4-1d-tight`, `v4-1d-t-btc-eth`. The v9 instances have no stop and are
  not affected.
- **Check:** fetch all 1m candles closed since the last check, not just the latest, so a
  wick is not missed when the Mac sleeps or a request is late. The first candle with
  `low <= stop` is recorded with an estimated fill `min(1m open, stop)` (catches a gap).
  Only the first hit per trade is recorded.
- **What is watched:** the main candle that is forming (the one after the engine's last
  processed candle). A long position with a stop is watched unless an exit is pending (it
  is sold at this candle's open). A pending entry is watched too: its stop is computed
  from the first minute's open exactly as the engine will (`Engine.entry_stop`). After
  the main close, one more check covers the last minutes before the engine processes the
  candle.
- **Table `stop_events` in `paper.db`:** `strategy, timeframe, symbol, entry_time, stop,
  minute_time, minute_open, minute_low, est_fill, detected_at`, one row per trade, keyed
  by `entry_time` (a pending entry has no trade id yet; `entry_time` matches the trade
  row the engine writes). `detected_at` is the wall clock, for the detection delay. At
  the main close the engine closes the trade as usual; `trades.exit_price` vs `est_fill`
  is the stop slippage.
- **Telegram:** a cursor on `stop_events` in the notify watcher, e.g. "⚠️ v4-1d-tight ·
  SOL/USDT stop 142.3 hit at 14:32 UTC, est. fill 141.9 (−0.3% vs stop). Paper closes it
  at the 1d close."
- **Panel:** a small "Stop events" table on the instance page; for closed trades, the
  engine's fill vs the estimated fill.
- **Out of scope:** the trailing exit (close-based by design; checking it intraday would
  change the strategy) and testnet (no stops).

## Order of work

1. `core/lower_timeframes.py` and the shadow stop (`trading/position_watch.py`, table,
   Telegram, panel).
2. Instance settings `lower_tf_entry` / `lower_tf_exit` and the hooks; replay check that
   existing instances are unchanged.
3. Pre-registration of E and X, then the backtest (sub-candle simulation for X).
4. Afterwards, to be re-evaluated: an SMA200 entry filter as a separate variant (see
   below).

## Later: other SMAs

Idea: check other SMAs (e.g. SMA200) as well. This belongs in the strategy, not the
watcher: a daily SMA200 only needs the daily candle (it would be a v9 parameter like
`entry_band`). Direction matters: a longer SMA as an **entry** filter (buy only in a
long-term uptrend) is a classic way to cut whipsaws; as an **exit** filter it would exit
later, the opposite of what X is for (an earlier exit would need a shorter SMA, close to
what T does). One pre-registered value, tested as its own variant after E and X.
