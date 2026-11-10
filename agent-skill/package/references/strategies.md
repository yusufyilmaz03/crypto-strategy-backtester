# Built-in strategies (v1-v10)

All strategies are long-only, read closed candles only, and return BUY, SELL or
"-" per candle. BUY opens a position at the next open if flat; SELL closes it at
the next open. BUY wins when both conditions are true. RSI and ATR use 14
candles with Wilder smoothing. `python scripts/backtest.py strategies` prints
this list from the code.

## Reference

| Key | Rule | Parameters (defaults) | Walk-forward grid |
|---|---|---|---|
| v1 | BUY: RSI < `rsi_buy` and EMA(`fast`) > EMA(`slow`). SELL: RSI > `rsi_sell` and EMA(`fast`) < EMA(`slow`) | rsi_buy=40, rsi_sell=60, fast=9, slow=21 | rsi_buy 30/35/40/45 x rsi_sell 55/60/65/70 (16) |
| v2 | v1's conditions, but they must hold on each of the last `confirm` candles | rsi_buy=45, rsi_sell=55, confirm=3, fast=9, slow=21 | rsi_buy 40/45/50 x rsi_sell 50/55/60 x confirm 2/3 (18) |
| v3 | Same rule as v1 with looser thresholds | rsi_buy=45, rsi_sell=55, fast=9, slow=21 | rsi_buy 40/45/50 x rsi_sell 50/55/60 (9) |
| v4 | Bollinger mean reversion. BUY: close < lower band and RSI < `rsi_buy`. SELL: close > upper band and RSI > `rsi_sell` | bb_len=20, bb_mult=2.0, rsi_buy=35, rsi_sell=65 | bb_mult 1.5/2.0/2.5 x rsi_buy 25/30/35/40 x rsi_sell 60/65/70 (36) |
| v5 | Trend following. BUY: EMA(`fast`) > EMA(`slow`), close > EMA(`slow`), RSI > `rsi_buy`. SELL: the mirror image with RSI < `rsi_sell` | fast=9, slow=21, rsi_buy=45, rsi_sell=55 | (fast, slow) 9/21, 12/26, 20/50 x rsi_buy 40/45/50/55 x rsi_sell 45/55 (24) |
| v6 | Donchian breakout. BUY: close above the previous `length`-candle high by more than `atr_thresh` x ATR. SELL: below the previous low by the same margin | length=20, atr_thresh=0.2 | length 10/20/40/55 x atr_thresh 0/0.2/0.5 (12) |
| v7 | Pullback in a trend. BUY: EMA(`trend_fast`) > EMA(`trend_slow`), RSI was below `oversold` within the last `lookback` candles and now crosses up through `buy_cross`. SELL: the mirror image in a downtrend | trend_fast=21, trend_slow=50, oversold=40, buy_cross=45, overbought=60, sell_cross=55, lookback=5 | (trend_fast, trend_slow) 21/50, 50/200 x oversold 30/35/40 x buy_cross 45/50 (12) |
| v8 | Time-series momentum. BUY while the return over `lookback` candles is positive, SELL when it is zero or negative | lookback=60 | lookback 20/60/120 (3) |
| v9 | Moving-average regime. BUY while SMA(`fast`) > SMA(`slow`) x (1 + `entry_band`), SELL when SMA(`fast`) < SMA(`slow`). `fast=1` means the close itself | fast=1, slow=200, entry_band=0.0 | (fast, slow) 1/50, 1/100, 1/200, 10/50, 20/100, 50/200 (6) |
| v10 | Turtle breakout. BUY: close above the highest high of the previous `entry` candles. SELL: close below the lowest low of the previous `exit_len` candles | entry=55, exit_len=20 | (entry, exit_len) 20/10, 55/20, 55/10, 100/50, 100/20 (5) |

Engine options that apply to any strategy: `--stop X` (stop-loss X x ATR below
the entry fill, checked against each candle's low; `none` by default) and
`--trail-atr X` (exit when the close falls X x ATR below the highest close
since entry).

## Mapping a description to a built-in

| The user says | Use | Notes |
|---|---|---|
| "Price above the 50-day (100, 200) moving average, out below it" | v9 `fast=1 slow=50` on 1d | The most common trend filter |
| "Golden cross / death cross", "50 crosses 200" | v9 `fast=50 slow=200` | v9 uses simple averages; for EMA crosses use v5 |
| "Enter only 2% above the average to avoid whipsaws" | v9 with `entry_band=0.02` | The exit stays at the average |
| "EMA 9/21 cross", "MACD-like trend following with EMAs" | v5 `fast=9 slow=21` | To drop the RSI filter: `rsi_buy=0 rsi_sell=100` |
| "Buy oversold RSI in an uptrend" | v1 (or v3 for looser levels) | "RSI below 30": `rsi_buy=30` |
| "Wait for N candles of confirmation" | v2 with `confirm=N` | |
| "Buy the dip to the lower Bollinger band", "mean reversion" | v4 | Band width is `bb_mult`, length `bb_len` |
| "Breakout above the 20-day high" | v10 `entry=20 exit_len=10`, or v6 for an ATR buffer | v6 exits on a breakdown of the same length; v10 has a separate exit length |
| "Turtle trading", "Donchian channel" | v10 `entry=55 exit_len=20` | |
| "Buy pullbacks in a trend when RSI recovers" | v7 | |
| "Momentum: hold if it is up over the last 3 months" | v8 `lookback=90` on 1d | |
| "With a 2 ATR stop" | add `--stop 2` | |
| "With a trailing stop" | add `--trail-atr 3` | ATR-based only; a fixed-percent trail needs a custom strategy |

Timeframe words: "daily" = 1d, "4-hour" = 4h, "hourly" = 1h. "N-day average"
on a 4h chart is N x 6 candles.

## When no built-in fits

Write a custom strategy (`assets/strategy_template.py`) only when the rule
needs something the table cannot express, for example: an indicator that is not
RSI, EMA/SMA, Bollinger, Donchian or ATR (MACD, Stochastic, volume filters); a
rule combining two built-ins; a time or weekday filter; a fixed-percent
take-profit.

These cannot be tested with this skill at all, and the user should be told so
instead of being given an approximation: short selling, leverage or futures,
grid and DCA bots, market making, order-book or tick strategies, portfolio
rotation across coins, anything that depends on news, on-chain or sentiment
data.

If a built-in is close but not identical, use it and state the difference
("your rule exits at RSI 70; v1 also requires the fast EMA to be below the slow
one"). Do not silently substitute.
