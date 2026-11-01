# Bankroll management (analysis written 2026-11-01, decisions added the same day)

Branch: `phase-4g-bankroll` (from `dev` at 44643d2).

Status: **implemented for paper (2026-11-01)**, see "As implemented" below. The
testnet work is listed at the end as a to-do.

## What was asked

1. Each instance gets a **starting bankroll**.
2. On a buy, the signal gets a **strength grade**, and the trade gets a **percentage of
   the bankroll** that depends on that grade.
3. When the bankroll runs low: a **Telegram notification**, plus a marker on the
   **overview** and on the **instance page**.
4. On a **very strong signal**, the option to **exit existing positions** to make room,
   choosing which ones by priority.
5. **Adding money** to the bankroll and **withdrawing** it, with every change logged.
6. A **history of the bankroll** (ledger).
7. **Fractional quantities** (e.g. buy 0.003 BTC) instead of whole units.

## Decisions (user, 2026-11-01)

- **One bankroll per instance.** Instances never share money. The bankroll belongs to
  the instance and is computed and managed inside the instance's own process loop.
- **Opt-in in the New instance modal.** Existing instances are not affected. A new
  sizing choice turns the bankroll on, and its settings appear in the modal.
- **"Low bankroll" is defined by the user** per instance, in the modal.
- **Withdrawals too.** Deposits and withdrawals only update the bankroll's books;
  nothing is sent to an exchange.
- **Stakes are a percentage of the starting bankroll**, with a **minimum and maximum
  entry amount** set in the modal.
- **Rotation has two options** in the modal: (1) close only positions that are in
  profit, (2) allow reducing a position instead of closing it.
- **Paper only** in this phase. Testnet steps go to the to-do list below.
- **Strength score:** built from the lower-timeframe and SMA(N) features of earlier
  phases, plus other sensible inputs. The scores are shown in the tables too.
- **Confirmed later the same day:** min/max entry are USDT amounts; the stake base
  follows deposits and withdrawals (contributed capital); the default values below are
  accepted. Rotation uses the free cash first and sells positions only for what is
  still missing.

## How money works today

### Paper

- `Instance.equity` is the capital **per symbol** (default 1000 USDT). An instance with
  42 symbols therefore starts with 42 000 USDT.
- Each symbol has its own `Engine`, whose `equity` is that symbol's own sleeve. The
  sleeves never share cash: a sleeve that lost money can't borrow from another, and a
  sleeve that is flat keeps its cash idle.
- On entry the engine puts its **whole sleeve** into the position:
  `qty = equity / (fill × (1 + fee))` (`core/engine.py`, `Engine._open`). `qty` is a
  float, so fractional quantities already work.
- `sizing = "inverse_vol"` (`PortfolioRunner` in `trading/run_realtime.py`) is the only
  place where money moves between symbols: once a month every sleeve is resized to
  weight × total value with `Engine.resize`.
- Instance returns (`app/reporting.py`, `curves`) are total equity ÷ (equity × number
  of symbols) − 1. That formula assumes no money is ever added or taken out.

### Testnet

- `Instance.equity` is the **total** capital. `Trader` splits it equally:
  `allocation = capital / len(symbols)`, kept per symbol in the `accounts` table.
- A buy is a **quote-amount market order**: the bot says "spend 23.80 USDT" and the
  exchange returns a fractional base quantity. A sell rounds the quantity to the
  symbol's LOT_SIZE step. Orders below the exchange minimum (about 5 USDT) are skipped.

### Item 7 is already done

Paper and testnet both trade fractional quantities. What paper lacks is **realism**: it
doesn't round to the LOT_SIZE step and has no minimum order. With bankroll stakes the
minimum matters, so a bankroll instance applies both (step and minimum from
`load_markets`, cached per symbol; a 5 USDT floor when the market info isn't available).

## What the running instances tell us

Concurrent open positions per paper instance, counted from `paper.db` on 2026-11-01:

| Instance | Symbols | Trades | Max open at once | Median open | Most entries on one candle |
|---|---|---|---|---|---|
| forward-v9 | 42 | 63 | 41 | 22 | 41 |
| v9-ivol-1d | 42 | 47 | 36 | 17 | 36 |
| v4-1d-tight | 42 | 20 | 15 | 2 | 14 |
| telegram-test | 42 | 16 | 6 | 1 | 11 |
| v9-trail-1d, v9-band-1d | 42 | 5–7 | 4–5 | 4 | 4 |

- **Signals arrive in batches.** On a 1d instance every symbol closes at the same moment,
  and forward-v9 got 41 BUY signals on one close. Processing them one by one would give
  the cash to whichever symbol comes first in the list. All BUY signals of one candle are
  therefore **collected, scored, ranked and then funded** in one step.
- **v9 is often heavily invested.** With stakes of a few percent the bankroll will often
  have no free cash in a bull phase. That is normal for a trend follower, which is why
  the low-bankroll setting lets the user pick what "low" means.
- **v9's BUY signal repeats** on every candle while the close stays above the SMA. A
  symbol that wasn't funded today is considered again at the next close, once cash is
  free. Nothing is lost by skipping it once.

## Research note

The bankroll rules change what an instance earns, not only how it is displayed. Earlier
tests found that v9's edge comes from a few large winners (win rate 11–24 %), that
rotation (R1) failed, and that rules which cut winners failed (X200, E + X, H + E + X).
Option (1) of rotation closes positions that are in profit, which is that kind of rule.
Bankroll instances therefore run as **untested variants**, like the `-ltf-cls` instances
were, and are labeled as such. A pre-registered archive test can follow if one of them
looks worth trusting; it needs the portfolio backtest from step 4 of the plan.

## Design

### 1. Instance setup

A third value of the existing **Sizing** select: `bankroll`, next to `equal` and
`inverse_vol`. Choosing it shows a **Bankroll** section in the New instance modal. The
other sizings hide it, so existing instances and the default stay exactly as today.

| Field | Meaning | Default | Locked after creation |
|---|---|---|---|
| Starting bankroll (USDT) | the instance's total cash at the start (the existing Equity field, relabeled "total" for this sizing) | 10 000 | yes |
| Stake per grade (% of base) | four numbers: Weak, Normal, Strong, Very strong; 0 means "don't buy" | 0 / 3 / 5 / 8 | yes |
| Min entry (USDT) | smaller stakes are skipped (never below 5) | 10 | yes |
| Max entry (USDT) | stakes are capped here | empty (no cap) | yes |
| Score inputs | checkboxes, see section 3 | trend SMA, lower TFs, distance, slope, momentum rank | yes |
| Rotation | master checkbox, see section 5 | off | yes |
| ↳ Only close positions in profit | rotation option 1 | off | yes |
| ↳ Allow reducing a position | rotation option 2 | off | yes |
| Low bankroll threshold | see section 6 | bankroll value below 70 % | no |

**Stake base.** "Percent of the starting bankroll" with deposits and withdrawals is read
as **contributed capital**: starting bankroll + deposits − withdrawals. Profits and
losses don't change stakes; a deposit raises them. This is an assumption to confirm.

Stake of one entry = base × grade % → capped at Max entry → capped at the free cash →
skipped if below Min entry (or the market's minimum).

Instance validation: `bankroll` is paper only; it can't be combined with `inverse_vol`
(they are values of the same field); rotation options need rotation on; the four
percentages must be 0–100 and non-decreasing.

### 2. The bankroll inside the instance process

- New runner `BankrollRunner` in `trading/run_realtime.py`, next to `PortfolioRunner`.
  It steps all symbols of the instance together per candle time, inside one
  transaction, in this order:
  1. fill the orders queued on the previous candle at this open (exits before entries,
     so their cash is available),
  2. stops and everything else `Engine.on_bar` does today,
  3. collect this close's BUY signals that can queue an entry, score them, rank them by
     score (ties: symbol name), and fund them from free cash,
  4. if rotation is on, fund what is still unfunded and graded "very strong" (section 5),
  5. write the ledger rows, the engine states and the bankroll state.
- **Engine reuse.** Each symbol keeps its `Engine`, with the same fill, fee, slippage,
  stop, trailing and lower-timeframe code. A flat engine holds 0 USDT. Funding an entry
  sets the flat engine's equity to the stake (a cash move only). The engine then queues
  the entry; if it isn't funded, `on_bar` is told `entry_ok=False` (the hook that rule E
  already uses). When a trade closes, the proceeds move back to the bankroll's free cash.
- **Reservation.** The stake is set aside at the signal close and spent at the next
  open. If the open price moved, the same USDT amount buys a slightly different
  quantity, as a quote-amount market order would.
- **Values the instance tracks:** free cash, reserved cash, invested (positions marked
  at the close), bankroll value = free + reserved + invested, contributed capital.
- **Cash freed inside a candle** (lower-timeframe exit, manual close) goes to free cash at
  once and can fund signals from the next close on.
- **Shared code:** scoring, grading, funding and rotation choice go in `core/bankroll.py`,
  so a later portfolio backtest uses the same functions, as `core/sizing.py` does for
  inverse volatility.
- **Restarts:** the bankroll state (cash, reserved, contributed, low-state flag) is saved
  with the engine states in the same transaction. `--rebuild-state` for a bankroll
  instance must replay all symbols together, including deposits, withdrawals and manual
  closes from the ledger; until that exists it refuses bankroll instances with a clear
  message.

### 3. Strength score

The score is computed for each BUY signal at its close, from closed candles only (the
lower-timeframe value is the one closed at that time, `lower_timeframes.signal_at`). Each
selected input gives 0 to 1 point; the score is the average × 100. Inputs:

| Input | Points | Source / note |
|---|---|---|
| **Trend SMA(N)** | 1 if the close is above SMA(N) | `trend_filter`; N from the modal (default 200) |
| **Lower timeframes agree** | 0.5 for each lower timeframe that says BUY | `load_signals` / `signal_at` (1d: 4h + 1h, 4h: 1h + 15m); the strategy's signal or the SMA(N) mode, as with E/X |
| **Distance above the SMA** | (close − SMA) / ATR: linear from 0 at 0 ATR to full at 2 ATR, full up to 4 ATR, 0.5 above 4 ATR (stretched) | the strategy's slow SMA for v9, the trend SMA otherwise |
| **SMA slope** | change of the SMA over the last 10 candles per candle: 0 when flat or falling, linear up to full at +0.5 % per candle | trend steepness |
| **Momentum rank** | percentile of the 60-candle return among the instance's symbols at this close | cross-sectional; uses the batch, no extra data |
| **Relative strength vs BTC** | 1 if the 60-candle return beats BTC's | BTC candles fetched once per close |
| **Breakout** | 1 if the close is the highest of the last 55 closes | Donchian idea of v10 |
| **Volume** | volume ÷ its 20-candle mean, full at 1.5 | the candles already have volume |
| **RSI zone** | 1 between 50 and 70, 0.5 between 70 and 80, 0 above 80 | `RSI` column exists |
| **Low volatility** | 1 − percentile of ATR % among the instance's symbols | inverse-volatility idea that passed `docs/v9-sizing-test.md` |

Grades from the score (fixed in code for the first version):

| Grade | Score |
|---|---|
| Weak | below 40 |
| Normal | 40–60 |
| Strong | 60–80 |
| Very strong | 80 and above |

Notes:

- Inputs that need lower-timeframe candles make the instance load them, as E and X do
  today, even when E and X are off.
- The score and grade are written to the `signals` table (new columns) and the ledger,
  so each grade's later results can be compared.
- The same function scores **held positions** at every close; rotation compares them.
- The thresholds and the scaling numbers above are starting values, not tuned on any
  data. They are fixed when coding starts and written into this document.

### 4. Stake and ranking example

Starting bankroll 10 000, stakes 0 / 3 / 5 / 8 %, min 10, max 600, free cash 1 200:

| Signal | Score | Grade | Stake asked | Funded |
|---|---|---|---|---|
| SOL | 86 | Very strong | 800 → max 600 | 600 |
| AVAX | 71 | Strong | 500 | 500 |
| LINK | 52 | Normal | 300 | 100 left → 100 |
| DOGE | 45 | Normal | 300 | 0 left → skipped, logged |
| PEPE | 31 | Weak | 0 | not bought (grade 0 %) |

When the last funded entry would get less than its stake, the rule above funds what is
left as long as it is above Min entry. An alternative is "all or nothing" per entry. The
first version uses "what is left".

### 5. Rotation (exiting for a very strong signal)

Runs only when rotation is on and a "very strong" signal is still unfunded after step 3.

Candidates are the open positions that:

- have a **lower current score** than the new signal by at least 20 points,
- were held at least 3 candles (no churn), and are not already queued to exit,
- with option 1 on: are **in profit** at this close (unrealized PnL after fees > 0).

Candidates are used in order of the lowest current score first, then the smallest
unrealized return. At most 2 rotations per candle.

- **Option 2 off:** the candidate is closed completely at the next open (reason
  `ROTATE`), and the proceeds fund the new entry at the same open.
- **Option 2 on:** the candidate is reduced only by what the new stake still needs; if
  the rest would fall below Min entry, it is closed completely instead. This needs a new
  engine order (partial exit at the next open). A partial exit writes its own row in
  `trades` (reason `TRIM`, the sold quantity, its share of the entry fee and its PnL),
  and the open trade keeps the remaining quantity.

The cost of a rotation (two fees and slippage) is visible in the ledger.

### 6. Low bankroll

The user sets the threshold in the modal: a **basis** and a **percentage of contributed
capital**.

| Basis | Low when | Meaning |
|---|---|---|
| Bankroll value | free + reserved + invested < X % of contributed capital | money was lost |
| Free cash | free cash < X % of contributed capital | little room for new trades |

States: **OK** and **Low**, plus an automatic **Can't trade** note when free cash is below
Min entry (information, no alarm when the basis is bankroll value). Leaving Low needs the
value to be 5 points above the threshold, so a balance moving around it doesn't flip the
state every candle. The threshold can be changed after creation (it doesn't change
trades).

Where it shows:

- **Telegram:** a message when the state changes (to Low and back to OK). The
  supervisor's `Watcher` already polls the databases every 30 s; it reads the bankroll
  state the same way and keeps its cursor in `notify_state.json`.
- **Overview card and Instances table:** a yellow "Low" badge next to the mode badge.
- **Instance page:** a bankroll card above the metrics (free, reserved, invested, value,
  contributed) with the Low banner and the reason.
- **Telegram `/status` and instance view:** the same state.

### 7. Deposits and withdrawals

- **Panel:** "Deposit" and "Withdraw" buttons on the instance page (amount and note).
  A withdrawal can't exceed the free cash: money in positions has to be sold first.
- Both only change the books. The panel files a request (new table
  `bankroll_requests`, like `close_requests`), and the instance's process applies it at
  its next check (every minute, together with manual close) and writes the ledger row.
  The process owns the cash, so nothing races with a candle being processed. A stopped
  instance applies the request when it starts.
- Telegram commands for these can come later; they are not part of this phase.
- **Returns must ignore cash flows.** The return becomes a **time-weighted return**: the
  returns between cash flows are chained, so a deposit isn't profit and a withdrawal
  isn't a loss. The instance page shows **PnL in USDT** and **net contributed** next to
  it. Max DD and Sharpe use the time-weighted curve. The B&H comparison stays as it is.

### 8. Ledger (bankroll history)

New table `bankroll_ledger` in `paper.db`, one row per change of free cash, written in
the candle's transaction:

| Column | Example |
|---|---|
| id, strategy (run key), timeframe | |
| time | candle time, or the time a request was applied |
| kind | `INITIAL`, `DEPOSIT`, `WITHDRAW`, `RESERVE`, `BUY`, `SELL`, `TRIM`, `ROTATE`, `RELEASE`, `SKIP` |
| symbol, trade_id | for trade rows |
| amount | signed change of free cash (USDT) |
| fee | |
| free_after, reserved_after, invested_after, value_after, contributed_after | balances after the row |
| score, grade | for buys and skips |
| source | `system` or `panel` |
| note | deposit notes, skip reasons ("no free cash", "below min entry") |

The panel shows it in a **Bankroll** tab on the instance page, with a chart of free cash,
invested and bankroll value, and markers for deposits and withdrawals.

## How it fits the existing features

- **Existing instances:** unchanged. They don't get the new fields' effects, and their
  replay with the new code must stay identical (the usual check after an engine change).
- **Stop, trailing exit, lower-timeframe exit, manual close:** unchanged; their proceeds
  go back to free cash.
- **Trend filter and rule E** still block entries before scoring; the score only ranks
  what may enter.
- **Clone** copies the bankroll settings too, but not the ledger.
- **Instances table and Telegram setup line:** a "B" badge and the stake grades.

## As implemented (paper, 2026-11-01)

Code: `core/bankroll.py` (scores, grades, stakes, funding, rotation choice, low state,
time-weighted return), `trading/run_realtime.py` (`BankrollRunner`, `BankrollRequests`,
`market_rules`), engine additions in `core/engine.py`, tables in `trading/store.py`,
setup fields in `instances.py`, panel and Telegram views. Tests: `tests/test_bankroll.py`,
`tests/test_bankroll_app.py`.

Details that the design above left open:

- **Rotation order.** A Very strong signal that can't get its full stake doesn't take the
  partial stake at once. Free cash is used first; positions are sold only for the part
  that is still missing. Without a rotation plan the entry gets the free cash that is left
  (if it is above the minimum entry). The ledger note says how much came from each, e.g.
  "150.00 from free cash + 700.00 expected from rotating out AAA/USDT".
- **After a rotation** the sold symbol waits like after a trailing exit: it isn't bought
  back until its BUY signal resets or the close makes a new high.
- **A partial exit** (reason `TRIM`) is its own row in `trades` with the sold quantity, and
  counts as a closed trade in the win rate and realized PnL. The open trade's quantity is
  updated.
- **Lot size and minimum order.** Quantities are rounded down to the market's step from
  Binance's market info; the unused cents go back to free cash as a `RELEASE` ledger row.
  An entry whose rounded notional is below the market's minimum isn't opened; its stake
  goes back at the end of the candle (`RELEASE`, with the reason).
- **Ledger kinds:** `INITIAL`, `DEPOSIT`, `WITHDRAW`, `RESERVE` (stake set aside for the
  next open), `RELEASE` (unused stake back), `SELL` (any full exit), `ROTATE` (a position
  chosen for rotation; the sale itself is the next open's `ROTATE` row with the cash),
  `TRIM`, `SKIP` (a BUY that couldn't be funded, amount 0). There is no separate `BUY`
  row: the fill doesn't change free cash.
- **Low-bankroll badge** on the panel compares with the threshold directly; the Telegram
  message uses the 5-point hysteresis, so it doesn't repeat around the threshold.
- **The lower-timeframe score input** needs a 4h or 1d instance (the modal disables it
  otherwise); it loads the lower timeframes like E and X do. `rs_btc` fetches BTC's candles
  when BTC isn't one of the instance's symbols.
- **Not done:** `--rebuild-state` refuses bankroll instances (their symbols share one cash
  balance, so they can't be replayed one by one).

Checks:

- Existing instances are unchanged: `forward-v9`, `v2-test-1d`, `v4-1d-tight`,
  `v4-1d-t-btc-eth`, `v9-trail-1d` and `v9-band-1d` were replayed from a copy of `paper.db`
  with the new code and matched exactly. The paper loop of the old (`dev`) and new code
  gave identical signals, trades, equity, weights and engine states on 12 symbols × 400
  cached days for v9, trailing, band, v4 tight stop, v2, trend filter 200 and inverse
  volatility.
- A bankroll instance ran over 400 cached days (12 symbols, all inputs except the lower
  timeframes, rotation with trimming, a deposit on the way) and on the last five live days
  (with the lower-timeframe and rs_btc inputs): the ledger always added up to the free cash,
  free cash never went below 0, and the deposit didn't change the time-weighted return.
- Not checked in a browser yet.

## To-do for testnet (later, needs approval: order-sending code)

- Replace the equal `allocation` split in `Trader` with the instance bankroll; free
  cash, reserved and contributed capital in the testnet database.
- Rank and fund the batch of one close before sending orders; send sells before buys and
  send a rotation's buy only after its sell is filled, using the actual proceeds.
- Partial sells for rotation option 2, rounded to LOT_SIZE, with the dust rule.
- Check the exchange's free USDT against the bankroll's cash on start and before buys
  (like `reconcile` for positions); a warning when they differ.
- Deposits and withdrawals stay bookkeeping; the check above shows when the books and
  the exchange disagree.
- `RiskGuard`'s daily loss limit computed on the time-weighted return, so a withdrawal
  doesn't count as a loss.
- `MAX_ORDER_USDT` still caps every order, on top of Max entry.
- Rotation and trimming with real orders only after the paper results are reviewed.
