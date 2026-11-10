# Showcase and distribution options (2026-11-10)

Where this project could be shown or sold, prompted by an inbound listing offer
from Agensi.io. Facts were checked on 2026-11-10; platform terms change often,
so re-check before acting. Nothing here is legal or tax advice.

## Context

- The repo is public under the PolyForm Noncommercial License 1.0.0 (chosen
  2026-11-10): anyone may use, change and share the code for noncommercial
  purposes; commercial use needs a separate license from the author. Before
  that date there was no LICENSE file.
- The project is a full Python application (backtest engine, walk-forward
  research, paper trading, testnet order flow, Flask dashboard, Telegram).
  It is not an agent instruction set and not a strategy config for a
  third-party bot, which rules out some marketplaces.
- Market data comes from Binance public OHLCV endpoints via ccxt and needs no
  API key, so "we provide the data access" is not a selling point for us.
- Seller is resident in Türkiye. Businesses there cannot open a Stripe account
  directly, so the payout route is a deciding factor for every paid option.

## Agensi.io (inbound offer)

- Marketplace for SKILL.md skills used by AI coding agents (Claude Code,
  Codex, Cursor and others). Launched publicly around April 2026; small,
  early-stage team.
- Their answers to our questions (received 2026-11-10):
  - **Package:** a zip with SKILL.md at its root plus the scripts the agent
    runs. Not the whole application: one skill for one job, here "take the
    strategy the user describes, run a backtest or walk-forward test from the
    command line, report the results". The dashboard and Telegram stay on
    GitHub. They rate the example scenarios and reference files in the zip
    above SKILL.md itself.
  - **Data:** Binance public OHLCV is enough; their own data tools are not
    needed.
  - **Review:** every listing goes through an automated security scan and a
    manual review. The description must say "research tool, not financial
    advice". Orders must stay on testnet and live trading must be switched
    off in code.
  - **Payouts:** to Türkiye only through Zoneless, as USDC on the Solana
    network. A free listing needs no payout setup.
  - **Terms:** 70% of revenue to the creator, 30% to Agensi. No exclusivity.
- Fit: better than first judged. The skill format suits the research side of
  the project (engine, strategies, metrics, walk-forward), which is also its
  strongest part; the cost is one small package to maintain.
- **Decision (2026-11-10): start with a free listing.** Before any paid
  listing, ask an accountant: receiving USDC as income touches the central
  bank's 2021 ban on crypto payments and an unclear tax treatment.
- **Package:** `agent-skill/` (see its README; the folder is not tied to Agensi and can
  feed other skill marketplaces). `python agent-skill/build.py` writes
  `agent-skill/dist/crypto-strategy-backtester.zip`. It goes further
  than their rules require: no order code at all (not even testnet), no API
  key, no `.env`, no ccxt; candles come from the public klines endpoint with
  the standard library. The build fails if a scan finds anything else.
  Tested on 2026-11-10 from the zip in an empty folder with a fresh virtual
  environment (Python 3.13, pandas 3.1), all four example scenarios.
- License: the package ships the repository's LICENSE (PolyForm
  Noncommercial 1.0.0). Open: uploading the zip to Agensi.

## Selling

| Option | What gets sold | Fees / revenue share | Payouts to Türkiye | Fit |
|---|---|---|---|---|
| Apify Store | The backtester packaged as an "Actor" (input: symbols, strategy, timeframe; output: report) | Apify keeps 20%; pay-per-event or pay-per-usage pricing | PayPal or Wise (min $20), SWIFT wire (min $100); no country list found | Good: real usage-based marketplace for Python code |
| Lemon Squeezy | Own product page: a "pro" package or commercial license | Merchant-of-record fees | Türkiye listed for bank payouts | Good for an own storefront |
| Polar | Own product page, GitHub-oriented (licenses, sponsorships) | Merchant-of-record fees | Türkiye listed; payouts via Stripe Connect Express | Good for an own storefront; individual sellers must be supported by Stripe Express in the region |
| Gumroad | Downloadable package | Platform fees | Not confirmed for Türkiye | Possible; other Python trading bots are sold there |
| Cryptohopper Marketplace | Strategies, templates and signals for Cryptohopper's own bot | Fee not published; exclusive sellers pay half | Not checked | Poor: no custom Python code; strategies would have to be rebuilt in their editor |
| Agensi.io | Agent skill: backtest and walk-forward test from the command line (`agent-skill/`) | Creator keeps 70%; free listings allowed | Only via Zoneless, as USDC on Solana; not needed for a free listing | Fair for a free listing; paid only after tax advice |

Not a fit: MQL5 Market (MQL language) and TradingView (Pine Script).
Fiverr and Upwork sell services, not products; the repo works there as a
portfolio piece for custom trading-bot work.

## Showcase (free)

- **Show HN** and **r/algotrading:** lead with the research discipline rather
  than returns: walk-forward optimization, out-of-sample results, deflated
  Sharpe, cost stress test, survivorship and holdout tests, preregistered
  experiments (`docs/`). This audience values rigor and distrusts
  "profitable bot" claims.
- **Product Hunt:** fits once there is a product page and a clear offer.
- **Hugging Face Spaces (Docker):** a free public demo of the dashboard; the
  repo already has a Dockerfile.

## Before marketing

1. ~~Choose a license on purpose.~~ Done 2026-11-10: PolyForm Noncommercial
   1.0.0. A paid "pro" offer would be a separate commercial license from the
   author.
2. 35 commits are authored with the work email address. If the employment
   contract has an intellectual property clause, check it before selling.
   Set a personal `user.email` in this repo's git config going forward.
3. Keep the positioning as a research and education tool: testnet-only
   orders, live trading switched off in code, not financial advice.

## Suggested order

1. ~~License decision.~~ Done (PolyForm Noncommercial 1.0.0).
2. Free Agensi listing of the skill package.
3. Showcase: Show HN and r/algotrading, optionally a Hugging Face demo.
4. Paid "pro" package through Polar or Lemon Squeezy.
5. Extra channels: Apify Store; a paid Agensi listing only after the
   accountant's answer on USDC payouts.

## Sources

- Apify, Actor monetization: https://docs.apify.com/platform/actors/publishing/monetize
- Apify, monthly payouts: https://docs.apify.com/actors/publishing/monetize/monthly-payouts.md
- Lemon Squeezy, supported countries: https://docs.lemonsqueezy.com/help/getting-started/supported-countries
- Polar, supported countries: https://polar.sh/docs/merchant-of-record/supported-countries
- Stripe, global availability: https://stripe.com/en-cz/global
- Cryptohopper, how to sell: https://www.cryptohopper.com/ru/blog/7232-how-to-sell-on-cryptohopper
- Gumroad, getting paid: https://gumroad.com/help/article/13-getting-paid
- Agensi, about: https://www.agensi.io/about
