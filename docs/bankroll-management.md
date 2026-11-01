# Bankroll management (analysis written 2026-11-01)

Branch: `phase-4g-bankroll` (from `dev` at 44643d2).

Status: **analysis only, nothing is coded yet.** The open questions at the end need
answers before the first step starts.

## What was asked

1. Each instance gets a **starting bankroll** (one pool of cash for the instance).
2. On a buy, the signal gets a **strength grade**, and the trade gets a **percentage of
   the bankroll** that depends on that grade.
3. When the bankroll runs low: a **Telegram notification**, plus a marker on the
   **overview** and on the **instance page**.
4. On a **very strong signal**, the option to **exit existing positions** to make room,
   choosing which ones by priority.
5. **Adding money** to the bankroll, with every addition logged.
6. A **history of the bankroll** (ledger).
7. **Fractional quantities** (e.g. buy 0.003 BTC) instead of whole units.

## How money works today

### Paper

- `Instance.equity` is the capital **per symbol** (default 1000 USDT). An instance with
  42 symbols therefore starts with 42 000 USDT.
- Each symbol has its own `Engine`, whose `equity` is that symbol's own sleeve. The
  sleeves never share cash: a sleeve that lost money can't borrow from another, and a
  sleeve that is flat keeps its cash idle.
- On entry the engine puts its **whole sleeve** into the position:
  `qty = equity / (fill × (1 + fee))` (`core/engine.py`, `Engine._open`). `qty` is a
  float, so fractional quantities already work (0.003 BTC is normal).
- `sizing = "inverse_vol"` (`PortfolioRunner` in `trading/run_realtime.py`) is the only
  place where money moves between symbols: once a month every sleeve is resized to
  weight × total value with `Engine.resize`. Between rebalances the sleeves are
  independent again.
- Instance returns (`app/reporting.py`, `curves`) are total equity ÷ (equity × number
  of symbols) − 1. That formula assumes no money is ever added.

### Testnet

- `Instance.equity` is the **total** capital. `Trader` splits it equally:
  `allocation = capital / len(symbols)`, kept per symbol in the `accounts` table of the
  instance's database. Again no sharing between symbols.
- A buy is a **quote-amount market order** (`market_buy(symbol, quote_amount)`): we say
  "spend 23.80 USDT" and the exchange returns a fractional base quantity. A sell rounds
  the quantity with `amount_to_precision` (the symbol's LOT_SIZE step). Orders below the
  exchange minimum (`min_cost`, about 5 USDT) are skipped; a remainder below it is left
  as dust.
- `MAX_ORDER_USDT` caps every order; `RiskGuard` blocks new entries after a daily loss
  of `DAILY_LOSS_LIMIT_PCT` measured on total equity.

### Item 7 is already done

Both paper and testnet trade fractional quantities. What is missing is **realism in
paper**: paper doesn't round to the LOT_SIZE step and has no minimum order. With small
percentage stakes (for example 1% of 1000 USDT = 10 USDT) the 5 USDT minimum starts to
matter, so the pool mode should apply both in paper too (step and minimum from
`load_markets`, cached).

## What the running instances tell us

Concurrent open positions per paper instance, counted from `paper.db` on 2026-11-01:

| Instance | Symbols | Trades | Max open at once | Median open | Most entries on one candle |
|---|---|---|---|---|---|
| forward-v9 | 42 | 63 | 41 | 22 | 41 |
| v9-ivol-1d | 42 | 47 | 36 | 17 | 36 |
| v4-1d-tight | 42 | 20 | 15 | 2 | 14 |
| telegram-test | 42 | 16 | 6 | 1 | 11 |
| v9-trail-1d, v9-band-1d | 42 | 5–7 | 4–5 | 4 | 4 |

Two consequences for the design:

- **Signals arrive in batches.** On a 1d instance every symbol's candle closes at the
  same moment, and forward-v9 got 41 BUY signals on a single close. "First come, first
  served" would hand the cash to whatever symbol happens to be processed first
  (alphabetical order). All BUY signals of one candle must be **collected, ranked by
  strength and then funded**, in one step.
- **v9 is usually heavily invested.** A median of 22 open positions means a shared
  pool with stakes of a few percent is fully invested much of the time. "Bankroll low"
  will then be the normal state of a trend follower in a bull phase, not an alarm (see
  the low-balance section).

## Research caution (read before choosing the rules)

These rules change **what the strategy earns**, not just how it is displayed. The
research so far says:

- v9's edge comes from **a few large winners**: win rate 11–24 %, average win around
  +76 % in period B (`docs/v9-filters-test.md`). Every rule that cut winners failed
  (X200, E + X, H + E + X).
- **Rotation failed**: R1 cross-sectional rotation was negative with a deeper drawdown
  than buy and hold (`docs/phase2b-preregistration.md`). Exiting a position to buy a
  "stronger" one (item 4) is a form of rotation.
- v9's signal is **binary** (close above SMA50 or not). There is no strength in it
  today; a strength score is a new rule.
- Inverse-volatility sizing passed (`docs/v9-sizing-test.md`), so sizing *can* help,
  but only the pre-registered version was tested.

So the plan below builds the **mechanics** (pool, ledger, deposits, alerts, UI) so that
they don't change results by default, and treats the **strength grades** and **exiting
for a stronger signal** as rules that get a pre-registered backtest on the archive data,
like every earlier rule. Until then they can still run in paper instances, labeled as
untested, as was done with the `-ltf-cls` instances.

## Design

### 1. Bankroll pool

A new instance setting `capital = "pool"` (the existing behaviour is
`capital = "per_symbol"`, the default; old instances load as `per_symbol` and must replay
identically).

- `bankroll` (USDT): the starting cash of the whole instance, not per symbol.
- The instance owns **one cash balance**. A buy takes its stake out of cash; a sell
  (signal, stop, trailing, lower-timeframe, manual, rotation) puts the proceeds back.
- **Reservation:** the stake is decided at the signal close and **reserved** at that
  moment; the order fills at the next open (unchanged rule). Reserving prevents two
  decisions from spending the same cash. If the next open has moved, the stake buys a
  slightly different quantity; the USDT amount stays fixed, as with a quote-amount market
  order on the exchange.
- **Engine reuse:** each symbol keeps its `Engine`, but a flat engine holds 0. When an
  entry is queued, the engine's `equity` is set to the stake (`resize` while flat only
  moves cash). After a close, the engine's equity goes back to the pool. Engine fill,
  fee, slippage and stop rules stay the same code.
- **Processing order:** like `PortfolioRunner` today, all symbols are stepped together
  per candle time inside one transaction: fill pending orders at the open → exits →
  collect the new BUY signals → rank → fund → queue.
- **Pool equity** = free cash + reserved cash + Σ positions marked at the close. This is
  what the dashboard shows as the instance's equity.
- **Shared code:** sizing and ranking go in `core/` (e.g. `core/bankroll.py`) and are
  used by both the paper bot and a new portfolio backtest, as `core/sizing.py` is today.
  The current research backtests run symbols independently, so a portfolio backtest is
  needed anyway to test any of these rules.

`inverse_vol` and `pool` are different answers to the same question. The first version
should allow only one of them per instance.

### 2. Stake size and strength grades

**Percent of what?** "A percentage of the bankroll" has two readings, and they behave
very differently with 41 signals on one candle:

| Base | Stake for a 5 % grade | What happens with 41 signals |
|---|---|---|
| % of **free cash** | 5 % of the cash left | Every buy shrinks the next one: the 41st gets 5 % × 0.95⁴⁰ ≈ 0.64 % of the start. Order decides size. |
| % of **pool equity** (cash + positions) | 5 % of total value | 20 buys use up the cash; the other 21 get nothing. Ranking decides who gets in. |

Recommendation: **% of pool equity, capped by free cash**, ranked by strength. Stakes
then don't depend on processing order, and a strong signal gets the same size whether it
comes first or last.

Limits that apply to every stake:

- **Minimum order:** 5 USDT (Binance minimum notional). A stake below it is skipped and
  logged.
- **Maximum per position:** e.g. 20 % of pool equity, so one symbol can't dominate.
- **Cash reserve:** e.g. keep 5 % of pool equity in cash, unspendable by normal signals
  (fees, slippage, and the option in section 4).
- **Maximum open positions:** optional.

**Strength score.** A function `strength(df) -> float` per strategy, computed only from
the closed candle and earlier (no lookahead), mapped to grades. Candidates for v9:

| Input | Meaning | Note |
|---|---|---|
| (close − SMA50) / ATR | how far above the regime line, in volatility units | a large distance can also mean "late" |
| slope of SMA50 over n candles | is the trend itself rising | |
| return over 60 candles | momentum (v8's signal) | |
| close > SMA200 | long-term trend agrees | S200/E200 passed as filters |
| 4h and 1h agree | lower timeframes say BUY too | data already loaded for E/X |
| volume vs its 20-candle mean | participation | not in the indicator set yet |

Grades and stakes, as a starting point to discuss (not tested):

| Grade | Score | Stake (% of pool equity) |
|---|---|---|
| Weak | below 25th percentile | 2 % (or skip) |
| Normal | 25th–75th | 4 % |
| Strong | 75th–95th | 6 % |
| Very strong | above 95th | 8 % (may trigger section 4) |

Percentile thresholds must be fixed from the research period, never from the running
paper instances, and written down before the test.

**Sanity check for the code:** with one grade and a stake of 1 / N of pool equity, a
pool instance must come close to the equal-per-symbol instance (not identical: the pool
compounds jointly). This is a cheap test that the mechanics are right.

### 3. Low balance warning

There are two different situations, and only one is a problem:

- **Fully invested:** free cash is below the next stake because the money is in
  positions. Normal for v9; new signals are skipped. Information, not an alarm.
- **Capital lost:** pool equity is well below the net money put in (starting bankroll +
  deposits − withdrawals). This is the real warning.

Proposed states, evaluated after every candle:

| State | Condition (defaults to discuss) | Marker |
|---|---|---|
| OK | — | none |
| Fully invested | free cash < minimum order or < the smallest stake | grey "invested" badge |
| Low | pool equity < 80 % of net deposits | yellow badge |
| Critical | pool equity < 60 % of net deposits, or free cash and positions can't fund one minimum order | red badge |

- **Telegram:** only on a **change of state**, with a hysteresis (e.g. back to OK only
  above 85 %), so a balance moving around a threshold doesn't send a message every
  candle. The supervisor's `Watcher` already polls the databases every 30 s and keeps
  cursors in `notify_state.json`; it reads the bankroll state the same way.
- **Overview card and Instances table:** a badge next to the mode badge.
- **Instance page:** a banner above the metrics with free cash, invested, pool equity
  and the reason for the state.
- **Telegram `/status` and the instance view:** the same badge as an icon.
- Thresholds are instance settings that can be changed later (they don't change trades
  unless "Critical" also blocks entries, which should be a separate choice).

### 4. Exiting positions for a very strong signal

Only when **all** of these hold:

1. The new signal is graded "very strong" (or above a configured score).
2. Free cash (without the reserve) can't fund its stake.
3. A candidate position is clearly weaker: its **current** strength score is lower by at
   least a margin.
4. The candidate is not "young": held at least N candles (no churn).
5. At most K rotations per candle, and the instance option is on (default off).

Priority for choosing what to exit, first match first:

1. Positions whose own signal is already weak (score fell to the lowest grade).
2. Positions at a loss, the largest loss first.
3. Positions with the smallest gain since entry.
4. Never a position above a set gain (e.g. +30 %): v9 lives on its large winners.

Execution: the exit is queued like a signal exit (reason `ROTATE`, a new value next to
`SIGNAL`, `STOP`, `TRAIL`, `LTF`, `MANUAL`) and fills at the next open, then the new
entry fills at the same open with the proceeds. The cost of a rotation is two extra fees
and slippages; it is logged in the ledger. A lighter variant is **trimming** a position
instead of closing it.

Evidence so far is against this rule (rotation failed, cutting winners failed), so it
needs its own pre-registered test with the trade-level criteria from
`docs/v9-filters-test.md` (win rate, profit factor, expectancy, max DD).

### 5. Adding money

- **Panel:** an "Add funds" button on the instance page (amount + note), and optionally
  a withdrawal. **Telegram:** `/deposit NAME AMOUNT` with a confirm button, like Close.
- The panel doesn't change the balance itself. It files a request (new table, like
  `close_requests`), and the instance's own process applies it at its next check and
  writes the ledger row. The process owns the cash, so nothing races with a candle being
  processed. A stopped instance applies it when it starts.
- **Returns must ignore deposits.** Today return = equity ÷ start − 1; after a deposit
  that would show the deposit as profit. Use a **time-weighted return** (chain the
  returns between cash flows), and show **net deposits** and **PnL in USDT** separately.
  Max DD and Sharpe are computed on the time-weighted curve. The daily loss limit on
  testnet must also exclude deposits.
- **Testnet and later live:** a deposit is bookkeeping only, and the bot must check that
  the exchange's free USDT covers the pool's cash (a warning when it doesn't, as
  `reconcile` does for positions).

### 6. Bankroll history (ledger)

A new table, one row per cash movement, written in the same transaction as the candle:

| Column | Example |
|---|---|
| id, strategy (run key), timeframe | |
| time | candle time or request time |
| kind | `INITIAL`, `DEPOSIT`, `WITHDRAW`, `RESERVE`, `BUY`, `SELL`, `RELEASE`, `ROTATE` |
| symbol, trade_id | for trade rows |
| amount | signed USDT change of free cash |
| fee | |
| cash_after, reserved_after, equity_after | balances after the row |
| grade, score | for buys |
| source | `system`, `panel`, `telegram` |
| note | free text (deposits), skip reasons |

Plus a "skipped" row (amount 0) when a signal can't be funded, so it is visible why a
BUY didn't trade.

UI: a "Bankroll" tab on the instance page with the ledger table, and a chart of free
cash, invested and pool equity with deposit markers.

## How it fits the existing features

- **Locked setup:** `capital`, `bankroll` (starting amount), grade table, rotation
  options and limits are setup fields (locked after creation). Deposits, withdrawals and
  the warning thresholds are not.
- **Existing instances** stay `per_symbol` and must replay identically with the new
  code (the same check as after every engine change).
- **inverse_vol:** not combined with `pool` in the first version.
- **Stop, trailing exit, lower-timeframe exit, manual close:** they close through the
  engine as today; the proceeds go back to the pool. Cash freed inside a candle (X,
  manual) can fund signals from the next close on.
- **`--rebuild-state`:** has to replay the pool and the ledger (deposits included), not
  each symbol alone.
- **Testnet:** the pool replaces the equal `allocation` split; buys stay quote-amount
  orders. This is order-sending code and **needs approval before it is written**. Paper
  comes first.

## Open questions

1. **Percent of what:** pool equity capped by free cash (recommended) or free cash?
2. **Strength score:** which inputs for v9? Simple first (one input, e.g. distance above
   SMA50 in ATR), or a combination?
3. **Weak signals:** buy with a small stake, or skip?
4. **Testing:** build the mechanics first and test grades and rotation on the archive
   with a pre-registration before trusting them (recommended), or run them in paper
   right away as untested variants?
5. **"Low bankroll":** does it mean capital lost (equity below deposits) or no free cash
   for new trades? Which thresholds? Should "Critical" block new entries?
6. **Rotation:** keep it, or start with trimming / no rotation? Is there a gain above
   which a position is never exited?
7. **Withdrawals:** needed too, or deposits only?
8. **Scope:** paper only in this phase (recommended), testnet after approval?

## Proposed order

1. **Pool and ledger, paper only, one fixed stake** (no grades yet): `core/bankroll.py`,
   pool runner, ledger table, minimum order and LOT_SIZE rounding, time-weighted
   returns. Old instances replay identically; the 1 / N sanity check passes.
2. **Deposits, history, warnings:** request table, panel button and Bankroll tab,
   Telegram `/deposit`, state badges and notifications.
3. **Portfolio backtest and strength grades:** pre-register the score and grade table,
   test on archive periods A and B against the equal-weight pool.
4. **Rotation:** pre-registered test, same criteria.
5. **Testnet:** port the pool to `run_testnet.py`, after approval.
