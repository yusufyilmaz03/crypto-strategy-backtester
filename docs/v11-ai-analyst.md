# v11 AI analyst: an LLM plans the trades, the bot executes them (paper, forward only)

**Status: draft (2026-11-02; scored forecasts and model access added 2026-11-09).** The open questions at the end are still to be decided. Once
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
model was trained, with the model version pinned. The training data of both chosen models ends
on 2026-04-30 (OpenAI's model page).

## How it works

### When the model is called

- **Scheduled review:** once per symbol after each daily close (00:00 UTC + a few seconds).
  Whether the symbols go one per call or in fixed groups of five is chosen in the dry run (see
  "Grouped scheduled reviews").
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
  current plan and its reasons, the last few plans with their outcome, and the `memo` it wrote
  on the last call for this symbol. Without this it would change its mind on every call. Each
  call is a new, independent session: no conversation history is carried over, so every input
  is the recorded prompt and nothing else.
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
 "stop": 3250, "take_profit": 3900, "p_tp": 0.31, "grade": "normal",
 "review_if": [{"below": 3300}, {"above": 3650}],
 "reasons": ["..."], "news_ids": [123, 140],
 "memo": "Reconsider above a daily close over 3500; watch ETF flows."}
```

- `action`: `hold` (keep everything as it is), `enter` (place an entry order), `adjust`
  (change stop, take-profit or review levels of a position or a pending order), `cancel`
  (cancel a pending entry), `exit` (sell the position at market).
- `entry.type`: `market` or `limit`. `expires_candles`: how many daily closes the order
  lives.
- `stop`: required for every position and pending entry. `take_profit`: optional.
- `p_tp`: the model's probability that, if the entry fills, the take-profit is reached before
  the stop within 30 days of the fill. Required when an `enter` plan has a take-profit, and
  when an `adjust` changes the stop or take-profit of a pending entry; ignored otherwise. The
  prompt states this definition. Recorded and scored only: it never sizes, filters or changes
  a trade (see "Scored forecasts").
- `grade`: weak / normal / strong / very strong. Recorded; whether it sizes the position is
  open question 5.
- `review_if`: up to two price levels that trigger a review.
- `memo`: a note of at most 300 characters to itself, shown on the next call for the same
  symbol and then replaced. A note the model wants to keep has to be written again. Required,
  may be empty. It never affects orders.

### Guardrails (the validator)

A plan is applied only if it passes every check; the bounds were decided 2026-11-09 (open
question 6).
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
- `p_tp`, when required, present and between 0.01 and 0.99.
- `memo` at most 300 characters.
- Prices must be positive numbers with at most the symbol's price precision; anything that
  is not a valid plan, an API error or a timeout counts as `hold`.

Sizing, the daily loss limit and the kill switch stay in the existing deterministic code.

### Call limits

- At most 3 triggered reviews per symbol per day, and none within 60 minutes of the last
  call for that symbol. Beyond that, triggers are ignored until the next scheduled review.
- The model is reached through the user's ChatGPT plan (see "Model access"), so a call has no
  price. The plan's quota is not visible; the bot counts calls and tokens per day instead. A
  usage-limit error makes the remaining calls of the day `hold` and sends a Telegram message.
- A timeout or process error is retried once after 60 seconds; if the retry fails too, the call
  counts as `hold`. A usage-limit error and an invalid plan are not retried.
- If more than 10% of a day's calls fail, a Telegram warning is sent.

### Grouped scheduled reviews (chosen in the dry run)

Every call carries about 9,300 tokens of Codex instructions on top of our prompt (about 1,200
tokens per symbol), so one call per symbol spends most of its tokens on the same text. The
alternative is groups of five:

- The 20 symbols are split into four fixed groups by their volume rank on the start date (1–5,
  6–10, 11–15, 16–20). The groups do not change during the test.
- A scheduled review sends one group per call. The answer is a list with one plan per symbol,
  each validated on its own; a rejected plan affects only its symbol.
- Triggered reviews stay one symbol per call.
- A failed group call follows the retry rule above; if it still fails, the group's five symbols
  count as `hold`.
- Scheduled calls per day over the four instances: 80 one by one, 16 in groups. Input tokens:
  about 840,000 and 230,000.

**Choice in the dry run.** On the same days and inputs, the primary setup runs twice one symbol
per call and once in groups. Compared: how often plans agree (same action, and entry, stop and
take-profit within 0.5 daily ATR), rejected plans, tokens, time and usage-limit errors. Outcomes
are not looked at. Groups are used if grouped plans agree with the single-call plans about as
often as the two single-call runs agree with each other, and are rejected no more often;
otherwise one symbol per call. The choice is written here before this document is fixed.

### Model access (decided 2026-11-09)

- Three instances, each with its own model setup (see "Variants"):

  | Instance | Model | Reasoning effort | Role |
  |---|---|---|---|
  | `v11-astra-high` | `gpt-6-astra` | `high` | primary |
  | `v11-astra-medium` | `gpt-6-astra` | `medium` | secondary |
  | `v11-sol-high` | `gpt-6.1-sol` | `high` | secondary |

  All are called through Codex CLI `0.166.0` (`codex exec`) signed in with the user's ChatGPT
  plan. No API key.
- A review's inputs are built once and the same prompt goes to all three. The calls run one at a
  time, the primary's first, so a usage limit reaches the secondary instances first.
- Each call is one independent `codex exec` run: an empty working directory, `--ephemeral`,
  `--ignore-user-config`, `--ignore-rules`, read-only sandbox, web search off, update check off,
  every tool feature off (shell, browser, computer use, plugins, apps, sub-agents, image tools,
  skills, goals, hooks), `--output-schema` with the plan schema and `--json` for the events.
- The process gets a minimal environment (`PATH`, `HOME`, `CODEX_HOME`); the keys the bot loads
  from `.env` are never passed on. The `codex` path is absolute, because the launchd service has
  no nvm `PATH`.
- Before its first call the bot checks `codex --version`. Any other version stops the calls
  (they count as `hold`) and sends a Telegram message.
- One call at a time, 180-second timeout. With 20 symbols one by one, a scheduled round takes
  about 20 minutes and the primary's calls finish in about the first 5; groups make it shorter.
- A change of model, effort or Codex version ends the test of the instance it affects, whatever
  the reason (a quota problem included). The new setup runs as a new instance with its own 180
  days.

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
- `llm_calls`: time, symbol or group, kind (scheduled / triggered), prompt, raw response, the JSONL
  events, model id, reasoning effort, Codex CLI version, input, cached, output and reasoning
  tokens, latency, exit code, stderr, whether it was a retry.
- `llm_plans`: the parsed plan, whether it passed the validator and why not, the plan it
  replaced.
- `llm_orders`: type, price, created, expires, status, fill time and price.
- `llm_forecasts`: plan id, symbol, fill time and price, stop, take-profit, `p_tp`, outcome and
  how it ended (take-profit / stop / neither), resolve time.

Every plan can be replayed and audited. The model version is pinned; if the provider
retires or changes it, the test ends at that point and a new instance starts. The same holds
for a change of effort or Codex CLI version.

## Variants

- **A, planner:** everything above.
- **Model setups (decided 2026-11-09):** A runs three times, as `v11-astra-high` (primary),
  `v11-astra-medium` and `v11-sol-high` (table under "Model access"). Same prompts, validator and
  execution rules. The secondary instances show whether a lower effort or a smaller model plans
  differently, on the same days and the same inputs, so plans can be compared one to one.
- The no-news control runs with the primary setup. A0 and B do not run in this test (open
  question 8).
- **A0, direction only (open question 8):** same inputs and model, but the answer is only
  LONG or FLAT, filled at the next daily open like the other 1d instances. Shows whether
  planning adds anything over picking a direction.
- **B, v9 gate (open question 8):** v9's entries are taken only if the model says LONG on
  that candle; exits stay v9's. Shows whether the model adds anything to an existing rule.
- **No-news control (open question 8):** A without headlines. Shows whether the news adds
  anything.

## Evaluation

- Earliest evaluation: 180 days after the first plan.
- Benchmarks over the same period and symbols: equal-weight buy-and-hold, and v9
  (`close > SMA50`, its fixed rule) run by the engine on the same 20 symbols. `forward-v9` itself
  trades `config.SYMBOLS`, so it is shown for reference only.
- **Pass (A), for `v11-astra-high` only, all of:**
  - annualized Sharpe of daily returns above buy-and-hold's
  - max drawdown below buy-and-hold's
  - at least 30 closed trades
- **Pass (B):** Sharpe at least v9's and max drawdown at most v9's, same symbols and period,
  at least 30 closed trades.
- A0 and the no-news control are compared with A descriptively.
- `v11-astra-medium` and `v11-sol-high` get the same metrics and scored forecasts but no
  pass/fail: with three instances tested against buy-and-hold, one passes by luck more often than
  with one. They are compared with the primary descriptively, including plan by plan on the same
  days (same action, entry, stop, take-profit, `p_tp`).
- Reported, not pass/fail: return, closed trades, win rate, average win and loss, profit
  factor, expectancy per trade, exposure; how positions ended (stop / take-profit / model
  exit); limit fill rate; rejected plans and their reasons; scheduled and triggered calls;
  tokens per day; failed calls by reason (the ChatGPT plan has no per-call cost).

### Scored forecasts (reported, not pass/fail)

Returns depend on the market as well as the model and arrive slowly. This asks a narrower
question: does the model know how likely its own plan is to work? Every filled `enter` plan
with a take-profit, in A and in the no-news control, is one scored forecast. A0 and B give no
levels, so they have none.

- **Event:** from the fill, closed 1m candles are walked with the execution rules above,
  including stop first when both are hit in one candle. The stop and take-profit are the ones
  in force at the fill; a later `adjust` or model `exit` does not change the event.
- **Outcome:** 1 if the take-profit is reached first within 30 days, 0 if the stop is reached
  first or neither is reached. Every scored plan gets an outcome, so nothing drops out. Candles
  decide; no model judges the result.
- **Cut-off:** only fills at least 30 days before the evaluation date are scored, whatever
  their outcome, so plans that resolved quickly are not over-represented.
- **No-skill references:**
  - `P_placebo` (main): the plan's stop and take-profit as daily ATR multiples, applied from
    every daily close of the same symbol in the test window that has 30 days of data after it,
    with the same rules. Its hit rate carries the window's drift and the 30-day limit.
  - `P0 = (fill − stop) / (take_profit − stop)`: the chance for a driftless price with no time
    limit (0.26 for the example plan, filled at its limit). It is easy to beat in a rising
    market, so it is only a second reference.
- **Reported:** number of scored plans; Brier score of `p_tp`, `P_placebo` and `P0`; skill score
  `1 − Brier(p_tp) / Brier(reference)`; a calibration table with five probability bins (count,
  mean `p_tp`, observed rate); the share ending at take-profit, stop and neither; the share of
  entry plans without a take-profit, which are not scored and so could hide weak plans.
- **Uncertainty:** symbols move together, so plans from one date are not independent. Confidence
  intervals come from a bootstrap that resamples plan dates. Below 30 scored plans, only the
  counts are reported.

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
- Codex adds its own coding-agent instructions (about 9,300 tokens) before our prompt and they
  cannot be replaced. The pinned CLI version keeps them fixed.
- OpenAI's pages do not say whether `gpt-6-astra` is a fixed snapshot or how long it will be
  served. A change behind the same name might go unnoticed.
- The plan's quota is shared with the user's own ChatGPT and Codex use and is not visible. Days
  with a usage-limit error have no new plans. Three instances need three times the calls, and the
  same limit hits all of them; the call order protects the primary.
- OpenAI recommends API keys for automation and calls ChatGPT sign-in an advanced option.
- With groups, the model sees five symbols at once and may weigh them against each other, and
  scheduled and triggered reviews differ in format.

## Implementation outline (after the open questions are decided)

- `core/news.py`: fetch the feeds every 15 minutes, store into `news_items`, deduplicate by URL.
- `trading/llm_analyst.py`: build the inputs, call the model, validate, store the plan.
- `trading/order_sim.py`: the 1m order simulator above (entries, stops, take-profits,
  expiries, triggers), run from the minute loop; daily equity marks for the metrics.
- `analysis/forecasts.py`: resolve forecasts from 1m candles, the references, Brier and skill
  scores, calibration table, bootstrap by plan date.
- `llm/provider.py`: a small interface (prompt and schema in; plan, raw output and usage out),
  so tests use a fake provider and an API provider can be added later without touching the
  analyst.
- `llm/codex_cli.py`: the Codex provider from "Model access", with `subprocess.run` and a
  timeout.
- Model id, effort, Codex path and version, and the limits in settings. No API key.
- Tests: schema and validator (each rule, rejection keeps the old plan, stop only rises),
  fill rules (strict limit, gaps, stop before take-profit, no fill before the plan), call
  limits and budget stop, news time filter, restart replay gives the same fills, forecast
  outcomes (take-profit first, stop first, stop first in one candle, neither in 30 days, later
  `adjust` ignored, cut-off) and `p_tp` having no effect on orders.
- Codex provider tests with a fake `codex`: the command flags, the minimal environment, the version
  check, timeout and retry rules, usage-limit handling.
- Before this document is fixed: a dry run of 2–3 days on live data that measures only format
  validity, tokens, latency and usage-limit errors, and compares single and grouped calls (see
  "Grouped scheduled reviews"). Its plans are not traded or evaluated.

## Open questions

1. **LLM provider and model.** Decided 2026-11-09: Codex CLI `0.166.0` and the user's ChatGPT
   plan; primary `gpt-6-astra` with effort `high`, secondary `gpt-6-astra` `medium` and
   `gpt-6.1-sol` `high` (see "Model access").
2. **News source.** Decided 2026-11-09: the free RSS feeds of CoinDesk, Cointelegraph, The
   Block and Decrypt, stored by us, so the archive is ours.
3. **Universe.** Decided 2026-11-09: the 20 USDT spot pairs with the highest 30-day quote
   volume on Binance on the start date, chosen from all listed pairs (not only `config.SYMBOLS`)
   after the survivorship test's exclusions (stablecoins, fiat and gold tokens, leveraged tokens,
   tokenized stocks), fixed for the test. About 120 calls a day over the four instances; if the
   dry run hits the plan's usage limit, the number drops to 15 before this document is fixed.
4. **Budget.** Decided 2026-11-09: no USD budget (ChatGPT plan). Calls and tokens are counted per
   day, and a usage-limit error stops the day's calls (see "Call limits").
5. **Sizing.** Decided 2026-11-09: in the code, grade stakes are an instance option. With the
   bankroll on, the stake follows the model's `grade` (the bankroll's stake grades); with it off,
   a fixed slot per symbol (equal paper equity, the full slot per position, as `forward-v9`). The
   test instances run with the bankroll off, so they stay comparable with buy-and-hold and v9 and
   do not depend on the bankroll, which is still under review. The report adds a grade-stake
   equity curve computed from the primary's trades (2/5/7/10% of the starting capital per grade);
   it is an approximation, because a real bankroll can run short of cash after losses.
6. **Validator bounds.** Decided 2026-11-09: stop 0.5–4 ATR, limit at most 3 ATR below,
   take-profit at least 1 ATR, expiry at most 3 closes, review levels at least 0.5 ATR away.
7. **Call limits.** Decided 2026-11-09: 3 triggered reviews per symbol per day, 60-minute cooldown.
8. **Variants.** Decided 2026-11-09: the no-news control runs next to the three A instances, with
   the primary setup. A0 and B do not run in this test.
9. **Scheduled review frequency.** Decided 2026-11-09: daily.
10. **Forecast horizon.** Decided 2026-11-09: 30 days. A longer horizon leaves fewer "neither"
    outcomes, but the cut-off removes more of the latest plans from a 180-day test.
