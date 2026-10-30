# v9 research summary (as of 2026-10-30)

One page for everything tested on v9 so far: what each test asked, what it found, which
rules are candidates, and which paper instance follows which rule. The details,
designs and full tables are in the linked documents; every number here comes from them.

## In short

v9 (long while the daily close is above SMA50, out below it) does not beat
buy-and-hold by making more in booms; it beats it by losing far less in crashes. Every
refinement since then was tested on the same archive (2018-01 → 2026-09, every USDT pair
including delisted ones, point-in-time top 50) in two periods: **B** (2018-01 → 2022-09,
two booms and two crashes) and **A** (2022-10 → 2026-09, sideways and down).

- **Lead candidate: E200** (user decision, 2026-10-30): enter on v9's daily BUY only if
  the 4h and 1h closes are also above their own SMA200. It improved every trade metric,
  the drawdown and the return in both periods, and is the only rule so far whose Sharpe
  gain has a bootstrap interval above zero in both.
- **Lesson of the filter tests:** every filter raises the win rate, but rules that sell
  early do it by cutting v9's large winners, which lowers the profit per trade in a bull
  market. Entry filters improve the trades; early exits mostly shrink them.
- **Caveat:** no unseen data is left. All of this is supporting evidence; the forward
  paper test (evaluation 2027-03-29) is the real test.

## The tests, in order

| Date | Test | Document | Question | Result |
|---|---|---|---|---|
| 2026-10-12 | Survivorship-free test | `v9-survivorship-test.md` | Does v9 beat buy-and-hold with delisted coins included? | **PASS** (period B) |
| 2026-10-12 | Inverse-volatility sizing | `v9-sizing-test.md` | Do 1/σ weights beat equal weights? | **PASS** |
| 2026-10-25 | Exit variants | `v9-exit-test.md` | Trailing exit 3×ATR (T)? Entry band 2% (H)? | T **PASS**, H **PASS** |
| 2026-10-30 | Lower-timeframe rules | `v9-lower-tf-test.md` | Entry confirmation on 4h+1h (E)? Early exit on 4h+1h (X)? | E **FAIL**, X **PASS** |
| 2026-10-30 | Reliability filters | `v9-filters-test.md` | Judged by trades: trend SMA200 (S200), E and X in SMA200 mode, combinations | S200 **PASS**, E200 **PASS**, X200 / E+X / H+E+X **FAIL** |

The first four tests judged variants by Sharpe and max drawdown against the baseline.
The fifth judged them by their trades (win rate up; profit factor, expectancy and max
drawdown not worse), in both periods and with costs × 1.5.

## Baseline and survivorship

| Period B, top 50 | v9 | Buy-and-hold |
|---|---|---|
| Return | +676% | −29% |
| Sharpe | 1.09 | 0.43 |
| Max drawdown | 55.1% | 90.8% |

By year, v9 / buy-and-hold: 2018 −27% / −79%, 2019 +27% / −4%, 2020 +150% / +199%,
2021 +435% / +435%, 2022 (to September) −38% / −78%. In period A, the realistic universe
gave v9 −9% against buy-and-hold's −76%; on the 42 coins that survive today it would have
looked like +233% vs +23%: the survivorship bias is large.

## All variants by their trades

Closed trades entered while the coin was in the universe; returns after fees and
slippage. Win rate / profit factor / expectancy per trade / average win / average loss,
then the portfolio's max drawdown, return and Sharpe. Bold: the five primaries of the
filters test that passed. Rows marked † were judged by the earlier Sharpe/drawdown
criteria only; their trade metrics are shown for comparison.

**Period B (2018-01 → 2022-09)**

| Variant | Win rate | PF | Expect. | Avg win | Avg loss | Max DD | Return | Sharpe |
|---|---|---|---|---|---|---|---|---|
| Baseline v9 | 20.3% | 2.90 | +10.1% | +75.7% | −6.6% | 55.1% | +676% | 1.09 |
| H, band 2% † | 24.0% | 3.11 | +13.2% | +81.0% | −8.2% | 50.6% | +768% | 1.14 |
| T, trail 3×ATR † | 21.3% | 2.73 | +9.2% | +68.4% | −6.8% | 52.4% | +713% | 1.14 |
| E † | 21.9% | 3.16 | +12.0% | +79.8% | −7.1% | 51.8% | +834% | 1.17 |
| X † | 28.5% | 1.75 | +2.8% | +23.2% | −5.3% | 35.7% | +376% | 1.24 |
| **E200** | 23.3% | 3.33 | +13.7% | +83.8% | −7.6% | 51.7% | +901% | 1.20 |
| **S200** | 24.1% | 3.06 | +12.4% | +76.3% | −7.9% | 41.3% | +294% | 0.97 |
| X200 | 23.8% | 2.52 | +6.4% | +44.8% | −5.6% | 50.5% | +648% | 1.18 |
| E+X | 30.4% | 1.83 | +3.4% | +24.7% | −5.9% | 31.2% | +459% | 1.37 |
| H+E+X | 32.7% | 1.87 | +4.0% | +26.0% | −6.8% | 28.1% | +449% | 1.39 |

**Period A (2022-10 → 2026-09)**

| Variant | Win rate | PF | Expect. | Avg win | Avg loss | Max DD | Return | Sharpe |
|---|---|---|---|---|---|---|---|---|
| Baseline v9 | 15.7% | 1.08 | +0.39% | +32.5% | −5.6% | 64.0% | −9.2% | 0.14 |
| H, band 2% † | 18.3% | 1.12 | +0.69% | +36.6% | −7.4% | 63.3% | −5.3% | 0.16 |
| T, trail 3×ATR † | 16.3% | 1.09 | +0.42% | +31.6% | −5.7% | 60.0% | +0.5% | 0.19 |
| E † | 16.4% | 1.12 | +0.60% | +34.3% | −6.0% | 64.1% | −3.6% | 0.17 |
| X † | 25.2% | 1.13 | +0.45% | +14.9% | −4.4% | 32.6% | +27.4% | 0.39 |
| **E200** | 17.2% | 1.17 | +0.88% | +36.2% | −6.4% | 62.6% | +2.6% | 0.21 |
| **S200** | 18.4% | 1.10 | +0.57% | +33.8% | −6.9% | 53.8% | +3.1% | 0.17 |
| X200 | 18.9% | 1.15 | +0.57% | +23.3% | −4.7% | 54.1% | +7.9% | 0.22 |
| E+X | 26.1% | 1.18 | +0.63% | +16.2% | −4.9% | 32.0% | +36.5% | 0.47 |
| H+E+X | 28.9% | 1.23 | +0.96% | +18.0% | −5.9% | 29.6% | +45.8% | 0.57 |

Inverse-volatility sizing (weights, not trade rules): Sharpe B 1.09 → 1.17, A 0.14 → 0.45;
max drawdown B 55.1% → 54.2%, A 64.0% → 54.0%.

## What the tests taught

1. **v9 lives on a few large winners.** One trade in five wins, and the average winner
   (+76% in B) is more than ten times the average loser. Anything that sells those
   winners early (T a little, X200 more, X and every combination with X most) lowers the
   profit per trade in a bull market, even when it raises the win rate and the Sharpe.
2. **Early exits help in sideways and falling markets.** In period A every rule with X
   improves all trade metrics and halves the drawdown. Their value depends on the regime,
   which can't be known in advance; v9 itself already exits on the daily SMA50.
3. **Entry filters improve the trades themselves.** E, E200 and H skip some entries and
   keep the large winners. E200 does it best: the highest profit factor and expectancy
   in both periods, with v9's exposure and return intact.
4. **The length of an SMA filter matters.** E's profit factor and expectancy rose steadily
   from SMA50 to SMA100 to SMA200 on the lower timeframes; the daily trend filter (S100/S150/S200/S250) did not
   move in one direction, so S200's pass rests on one length.
5. **Win rate alone misleads.** It rose in every variant. The profit factor and the
   expectancy per trade are what separate a better trade from a shorter one.

## Candidates and decisions

| Rule | Status | Decided |
|---|---|---|
| **E200** | **Lead candidate** | User, 2026-10-30 |
| Inverse-volatility sizing | Candidate (passed) | `v9-sizing-test.md` |
| H, entry band 2% | Candidate (passed); close to E200 on trades, slightly behind on PF, expectancy and Sharpe | `v9-exit-test.md` |
| T, trailing exit 3×ATR | Passed its test; would fail the trade criteria in B | `v9-exit-test.md` |
| X, lower-timeframe exit | Passed its test; would fail the trade criteria in B | `v9-lower-tf-test.md` |
| S200, daily trend filter | Passed; set aside (halves the return in B, uneven neighbors) | User, 2026-10-30 |
| E (4h+1h, v9 signal) | Failed (max drawdown in A, by 0.09 points) | `v9-lower-tf-test.md` |
| X200, E+X, H+E+X | Failed (profit factor and expectancy in B) | `v9-filters-test.md` |

Combinations of E200 with other rules are not tested on purpose: the archive has no
unseen data left, and the obvious partners either cut winners (T, X) or address the
same problem (H). Revisit after the forward evaluation.

## Paper instances

All started before the test that decided their variant, so their forward data was not
chosen after the fact. Same setup as `forward-v9` (v9 `fast=1, slow=50`, 42 symbols,
1000 USDT each, daily) unless noted.

| Instance | Rule | State |
|---|---|---|
| `forward-v9` | baseline, the pre-registered forward test (since 2026-09-30) | running |
| `v9-e200-1d` | **E200** | running |
| `v9-ivol-1d` | inverse-volatility sizing | running |
| `v9-band-1d` | H | running |
| `v9-trail-1d` | T | running |
| `v9-ltf-exit-1d` | X | running |
| `v9-s200-1d` | S200 (set aside) | running; can be stopped |
| `v9-ltf-entry-1d`, `v9-x200-1d`, `v9-ex-1d`, `v9-hex-1d` | E, X200, E+X, H+E+X | stopped (failed), records kept |
| `v2-test-1d`, `v4-1d-tight`, `v4-1d-t-btc-eth` | older strategies with stops; feed the shadow stop | running |

## Next

- **~2026-11-30:** first look at the shadow stop data (`TODO.md`).
- **2027-03-29:** forward evaluation of `forward-v9`, then a descriptive comparison of
  the variants above, by trades (win rate, profit factor, expectancy) as well as by
  return and drawdown (`TODO.md`).
- Only after that: one pre-registered combination with E200, if the forward data
  supports E200.
