# Agent skill package

An agent skill (SKILL.md format, for Claude Code, Codex, Cursor and similar
agents) that wraps this repository's backtest engine for one job: take a
strategy the user describes, run a backtest or walk-forward test on Binance
public candles, and report the result honestly. The dashboard, paper trading,
testnet trading and Telegram stay in the repository and are not part of it.
The zip is not tied to one marketplace: it can be installed by hand as a skill
folder or listed on a skill marketplace (the first one planned is Agensi.io,
see `docs/showcase-and-distribution.md`).

Research and education only. Not financial advice.

## Layout

```
agent-skill/
  build.py      builds dist/crypto-strategy-backtester.zip (SKILL.md at the zip root)
  package/      the package source: SKILL.md, scripts/, references/, examples/, assets/
  overlay/      skill versions of kripto_bot/paths.py and kripto_bot/core/data.py
  dist/         build output (gitignored)
```

`build.py` copies these files from the repository unchanged into
`scripts/kripto_bot/`: `core/indicators.py`, `core/strategy.py`,
`core/engine.py`, `core/metrics.py`, `analysis/evaluation.py`,
`analysis/optimize.py`. The skill therefore runs the same engine, strategies
and metrics the test suite covers. Rebuild after changing any of them.

The two overlay files replace their repository counterparts:

- `core/data.py` downloads klines with the standard library from
  `data-api.binance.vision` (fallback `api.binance.com`) instead of ccxt. Same
  interface, same closed-candle rule, pandas is the only heavy dependency.
- `paths.py` only names the candle cache folder
  (`~/.cache/crypto-strategy-backtester/ohlcv`).

## What is deliberately not in the package

No `config.py` (it loads `.env`), no `trading/`, `app/`, `llm/`, `tools/`, no
ccxt, no python-dotenv. The build's safety scan parses every packaged Python
file and fails on: imports outside a short allowlist, network modules anywhere
but the data module, URLs other than the two Binance market-data hosts,
credential or order words in names and strings, environment access, and
`eval`/`exec`/file deletion calls.

## Build and test

```bash
python agent-skill/build.py
```

Test the zip the way a buyer's agent would use it, in an empty folder with a
fresh virtual environment:

```bash
mkdir /tmp/skill-test && cd /tmp/skill-test
unzip /path/to/kripto-bot/agent-skill/dist/crypto-strategy-backtester.zip -d skill
python3 -m venv venv && venv/bin/pip install -r skill/requirements.txt
venv/bin/python skill/scripts/backtest.py backtest --symbols BTC/USDT ETH/USDT --timeframe 1d \
    --days 1460 --end 2026-11-01 --strategy v9 --params fast=1 slow=50
```

The output should match `package/examples/01-moving-average-filter.md`.

## License and listing text

- The package ships the repository's `LICENSE` (PolyForm Noncommercial 1.0.0);
  `build.py` copies it to the zip root and fails without it.
- The listing description must carry "Research and education only. Not
  financial advice."
