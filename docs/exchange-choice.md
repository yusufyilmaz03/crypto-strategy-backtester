# Exchange for Phase 5: Binance Global or Binance TR (2026-10-12)

Phase 5 (live trading with small capital) needs one exchange account. This note
compares the two options for a trader resident in Türkiye. Facts checked on
2026-10-12; regulatory points are not legal advice.

## Comparison

| | Binance Global (binance.com) | Binance TR (binance.tr) |
|---|---|---|
| API | `api.binance.com`, `/api/v3/...`, symbols like `BTCUSDT` | own API: `/open/v1/...`, symbols like `BTC_TRY` |
| Support in this project | ready: ccxt `binance`, the same code as the testnet instance | not in ccxt: needs a new broker adapter and its tests |
| Testnet | yes (the one we use), same API as live | none documented, so Phase 4 can't be repeated on it |
| Markets | all USDT pairs; all 42 of our symbols | TRY pairs only (309 via the public API); 40 of our 42 bases (no ILV, EPIC) |
| Spot fees | 0.10% maker/taker, 0.075% when paid in BNB | crypto pairs 0.10%/0.10%, TRY pairs reported as 0.10%/0.15% (verify on binance.tr) |
| Cash while out of the market | USDT | TRY, unless converted to USDT, which costs an extra fill on every trade |
| Price used by signals | the same USDT candles the strategy was tested on | TRY candles: USDT price × USD/TRY plus a local premium; thinner order books |
| Turkish lira deposits | not directly; usually via Binance TR or P2P, then a USDT transfer | direct |
| Regulation for Turkish residents | Law 7518 (2 July 2024) requires an SPK licence to serve Turkish residents; Binance removed its Turkish interface on 27 Sept 2024 and said services stay available. Using it on your own initiative is a grey area that may change | the local, SPK-regulated route (check SPK's list for its current licence status) |

## What it means for this bot

- **Global** fits the bot as built: no new code, the same API as the testnet where
  the order flow was verified, and live prices match the candles the strategy was
  researched on. The open question is regulatory, not technical.
- **TR** is the regulated route but a different setup. The strategy would trade TRY
  pairs while its signals and research come from USDT pairs. While v9 is out of the
  market (often more than half the time) cash would sit in TRY and lose value
  against USD, unless every trade goes through USDT_TRY, which doubles the fills
  and their cost. That changes the strategy's returns enough that it would need its
  own backtest (TRY pairs, TRY cash) before going live, plus a broker adapter tested
  without a testnet.

## Recommendation

Technically, Binance Global: Phase 5 can start from the code and tests we have, and
the backtest, paper and testnet results apply to it directly. Fund it with a small
amount through Binance TR (TRY → USDT → transfer). Because of the regulatory grey
area the choice is the account holder's; if the regulated route is preferred, the
work for Binance TR is: a TR broker adapter, a TRY-pair backtest of v9 including the
cash currency effect, and a tiny-size live trial instead of a testnet phase.

## Sources

- Binance TR open API documentation: https://www.binance.tr/apidocs/
- Binance TR public symbol list: https://www.binance.tr/open/v1/common/symbols (queried 2026-10-12)
- Binance removes Turkish language (FinanceFeeds): https://financefeeds.com/binance-removes-turkish-language-to-comply-with-new-turkiye-regulations/
- Turkey crypto rules overview (Bitget Academy): https://www.bitget.com/academy/are-there-any-specific-regulations-for-binance-users-in-turkey-2026-comprehensive-guide
- Binance TR fee comparison (Coinbalina): https://coinbalina.com/binance-tr-komisyon-hesaplama/
