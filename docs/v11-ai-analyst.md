# v11 AI analyst: an LLM plans the trades, the bot executes them (paper, forward only)

**Status: draft (2026-11-02).** The open questions at the end are still to be decided. Once
they are, this document is fixed as the pre-registration, before the first plan is made.
After that, rules and criteria do not change; a change that alters trading behavior
restarts the test and is noted here.

## Why

Every strategy so far is a fixed rule on prices. v11 asks a different question: can a
language model that reads closed-candle market data and recent news make trading plans
(where to enter, where to stop, where to take profit, when to look again) that do better
than buy-and-hold and v9?

The instance gets no strategy signals. The model writes a plan per symbol; the bot checks
the plan against fixed limits and executes it. The bot is the model's engine, with
guardrails.

Public evidence is thin. In nof1's Alpha Arena season 1 (2025-10-18 to 11-03, six models,
10,000 USD each, crypto perpetuals) only two models finished in profit, and the organizers
warn the result is too short and uncontrolled to rank models. CryptoTrade (EMNLP 2024)
reports mixed results against buy-and-hold over short test windows. No open-source LLM
trading project has an independently verified live record (notes in
`docs/research/010_github-genel-arastirma.md`, local only).

## Why there is no backtest

A model's training data contains the history we would test on: prices, news and what
happened next. A prompt dated in the past lets the model "remember" the outcome.
Look-Ahead-Bench (arXiv 2601.13770) measures this kind of leak: standard models lose much
of their in-sample edge out of sample. A backtest of v11 would therefore not be evidence,
so none is run. The only test is forward paper trading on data that did not exist when the
model was trained, with the model version pinned.

## How it works

### When the model is called

- **Scheduled review:** once per symbol after each daily close (00:00 UTC + a few seconds).
- **Triggered review:** when the price crosses a level the model asked for in its last plan
  (`review_if`), checked on closed 1m candles.
- Nothing else calls the model. Fills, stops and take-profits are executed by the bot
  without asking it.

### What the model sees

- Market summary from closed candles: returns over 1, 7, 30 and 90 days, distance from
  SMA50 and SMA200, daily ATR(14) as % of price, the last 20 daily highs and lows, 30-day
  volume change, the last price, and the same summary for BTC as market context. Numbers,
  not chart images.
- Market sentiment and structure: the crypto Fear & Greed index (alternative.me, daily since
  2018-02-01; last 7 values) and BTC and ETH dominance (CoinGecko `/global`; only the current
  value is free, so the bot records it daily from now on and gives the last 7 days it has).
- Its own state: the position (flat, or long since when, at what price), open orders, the
  current plan and its reasons, and the last few plans with their outcome. Without this it
  would change its mind on every call.
- News headlines from the last 72 hours that mention the coin, plus general market
  headlines: source, published time, title, short summary. An item is used only if both
  its published time and the time we fetched it are before the call (guards against
  back-dated items).
- The limits below, so it can plan inside them.
- No web access or other tools.

### What the model returns

One plan per symbol, strict JSON validated against a schema:

```json
{"symbol": "ETH/USDT", "action": "enter",
 "entry": {"type": "limit", "price": 3420, "expires_candles": 1},
 "stop": 3250, "take_profit": 3900, "grade": "normal",
 "review_if": [{"below": 3300}, {"above": 3650}],
 "reasons": ["..."], "news_ids": [123, 140]}
```

- `action`: `hold` (keep everything as it is), `enter` (place an entry order), `adjust`
  (change stop, take-profit or review levels of a position or a pending order), `cancel`
  (cancel a pending entry), `exit` (sell the position at market).
- `entry.type`: `market` or `limit`. `expires_candles`: how many daily closes the order
  lives.
- `stop`: required for every position and pending entry. `take_profit`: optional.
- `grade`: weak / normal / strong / very strong. Recorded; whether it sizes the position is
  open question 5.
- `review_if`: up to two price levels that trigger a review.

### Guardrails (the validator)

A plan is applied only if it passes every check; the bounds are proposals (open question 6).
A plan that fails is rejected as a whole and the previous plan stays in force. It is never
corrected silently, because a corrected plan would no longer be the model's decision. The
rejection and its reason are stored.

- Long only, one position per symbol, no adding to a position, no leverage.
- Stop below the entry (or below the last price for a position), between 0.5 and 4 daily
  ATRs away.
- A stop can only be raised, never lowered.
- Limit buy at or below the last price and at most 3 daily ATRs below it. A "limit" above
  the last price is rejected (it would be a market order with extra steps).
- Take-profit, if given, at least 1 daily ATR above the entry.
- Entry orders expire after at most 3 daily closes.
- `review_if` levels at least 0.5 daily ATRs from the last price.
- Prices must be positive numbers with at most the symbol's price precision; anything that
  is not a valid plan, an API error or a timeout counts as `hold`.

Sizing, the daily loss limit and the kill switch stay in the existing deterministic code.

### Call limits

- At most 3 triggered reviews per symbol per day, and none within 60 minutes of the last
  call for that symbol. Beyond that, triggers are ignored until the next scheduled review.
- A hard daily budget in USD. When it is reached, the remaining calls of the day are skipped
  (counts as `hold`) and a Telegram message is sent.
- If more than 10% of a day's calls fail, a Telegram warning is sent.

### Execution (paper)

Orders are simulated on closed 1m candles, checked every minute (the same loop the shadow
stop and the lower-timeframe exit use). Fees 0.1% per fill; slippage 5 bps on market,
stop and gap fills.

- A plan takes effect from the first 1m candle that opens after the response is stored.
  No fill can happen before the plan existed.
- **Market entry or exit:** the open of that first 1m candle, plus slippage.
- **Limit buy:** fills at the limit when a 1m candle's low goes *below* it (touching is not
  enough: a real order waits in a queue). If a 1m candle opens below the limit, it fills at
  that open.
- **Stop:** when a 1m low reaches the stop, at min(1m open, stop) minus slippage.
- **Take-profit:** when a 1m high goes above it, at the take-profit (open if it gaps above).
- Stop and take-profit in the same 1m candle: the stop is assumed first.
- Expired entry orders are cancelled at the daily close.
- After a restart, standing orders are replayed over the missed 1m candles with the same
  rules. Missed triggered reviews are not made up; the next scheduled review covers them.

This makes v11 react within about a minute, unlike the other instances, which act only at
their candle close. How optimistic these fill rules are is partly visible in the shadow
stop data (`stop_events`, first look ~2026-11-30).

### Records

New tables in the instance's database:

- `news_items`: id, source, url, title, summary, published_at, fetched_at.
- `llm_calls`: time, symbol, kind (scheduled / triggered), prompt, raw response, model id and
  version, sampling settings, input and output tokens, cost, latency.
- `llm_plans`: the parsed plan, whether it passed the validator and why not, the plan it
  replaced.
- `llm_orders`: type, price, created, expires, status, fill time and price.

Every plan can be replayed and audited. The model version is pinned; if the provider
retires or changes it, the test ends at that point and a new instance starts.

## Variants

- **A, planner:** everything above.
- **A0, direction only (open question 8):** same inputs and model, but the answer is only
  LONG or FLAT, filled at the next daily open like the other 1d instances. Shows whether
  planning adds anything over picking a direction.
- **B, v9 gate (open question 8):** v9's entries are taken only if the model says LONG on
  that candle; exits stay v9's. Shows whether the model adds anything to an existing rule.
- **No-news control (open question 8):** A without headlines. Shows whether the news adds
  anything.

## Evaluation

- Earliest evaluation: 180 days after the first plan.
- Benchmarks over the same period and symbols: equal-weight buy-and-hold, and the v9 paper
  instance (`forward-v9`).
- **Pass (A), all of:**
  - annualized Sharpe of daily returns above buy-and-hold's
  - max drawdown below buy-and-hold's
  - at least 30 closed trades
- **Pass (B):** Sharpe at least v9's and max drawdown at most v9's, same symbols and period,
  at least 30 closed trades.
- A0 and the no-news control are compared with A descriptively.
- Reported, not pass/fail: return, closed trades, win rate, average win and loss, profit
  factor, expectancy per trade, exposure; how positions ended (stop / take-profit / model
  exit); limit fill rate; rejected plans and their reasons; scheduled and triggered calls;
  total LLM cost, and return after LLM cost as a % of the paper equity.

Passing is necessary, not sufficient: 180 days is one or two market regimes. Nothing in this
test sends orders. On the testnet, limit, stop and take-profit orders would be real exchange
orders (the "exchange-side stop / OCO" item in TODO.md), which needs explicit approval.

## Known limits

- One forward window: a pass can be luck, a fail can be a bad regime for the approach.
- Model output varies between calls even with fixed settings; the stored plan is the record,
  not a re-run.
- Fills on 1m candles are still an approximation: no order book, no queue position, no
  partial fills.
- More trades than v9 means costs matter more.
- News coverage is uneven: large coins get many headlines, small ones few or none. Free feeds
  can change or disappear during the test; a lost source is noted here.
- LLM cost is real even though trading is paper.

## Implementation outline (after the open questions are decided)

- `core/news.py`: fetch the feeds every 15 minutes, store into `news_items`, deduplicate by URL.
- `trading/llm_analyst.py`: build the inputs, call the model, validate, store the plan.
- `trading/order_sim.py`: the 1m order simulator above (entries, stops, take-profits,
  expiries, triggers), run from the minute loop; daily equity marks for the metrics.
- API key from `.env` (added by the user); model id, budget and limits in settings.
- Tests: schema and validator (each rule, rejection keeps the old plan, stop only rises),
  fill rules (strict limit, gaps, stop before take-profit, no fill before the plan), call
  limits and budget stop, news time filter, restart replay gives the same fills.

## Open questions

1. **LLM provider and model.** Which provider and exact model version. The key goes into
   `.env`, added by the user. Proposal: one current mid-size model with a pinned, dated
   version; price and limits checked when the code is written.
2. **News source.** Free RSS feeds of several outlets (e.g. CoinDesk, Cointelegraph, The
   Block, Decrypt), or a news API with its own timestamps and archive. Proposal: RSS, stored
   by us, so the archive is ours.
3. **Universe.** All 41 symbols, or a fixed subset. Proposal: the 10 most liquid of
   `config.SYMBOLS` by 30-day quote volume on the start date, fixed for the test.
4. **Budget.** Daily cap in USD. Roughly (symbols × (1 scheduled + triggered calls) × tokens per
   call × price); estimated once the model and universe are chosen.
5. **Sizing.** A fixed slot per symbol (equal paper equity, the full slot per position, as
   `forward-v9`), or the bankroll's stake grades chosen by the model's `grade`. Proposal: fixed
   slot (simpler, comparable to v9, the bankroll is still untested); the grade is recorded.
6. **Validator bounds.** Confirm or change: stop 0.5–4 ATR, limit at most 3 ATR below, take-profit
   at least 1 ATR, expiry at most 3 closes, review levels at least 0.5 ATR away.
7. **Call limits.** Confirm or change: 3 triggered reviews per symbol per day, 60-minute cooldown.
8. **Variants.** Which of A0 (direction only), B (v9 gate) and the no-news control run next to A.
   B is cheap (calls only on v9 entry candles); A0 adds one call per symbol per day (as many
   as A's scheduled reviews); no-news adds about as many calls as A, with shorter prompts.
9. **Scheduled review frequency.** Daily (proposed) or every 4h.
