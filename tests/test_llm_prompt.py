import numpy as np
import pandas as pd
import pytest

from kripto_bot.llm import prompt as pr


def _daily(n=250, start=100.0, step=1.0, volume=10.0):
    close = start + step * np.arange(n)
    return pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=n, freq="D"),
                         "open": close, "high": close + 2, "low": close - 2, "close": close,
                         "volume": np.full(n, volume)})


def test_market_summary_numbers():
    df = _daily()
    s = pr.market_summary(df)
    last = 100 + 249
    assert s["last"] == last
    assert s["ret_1d"] == pytest.approx(last / (last - 1) - 1)
    assert s["ret_90d"] == pytest.approx(last / (last - 90) - 1)
    assert s["sma50_dist"] == pytest.approx(last / np.mean(np.arange(200, 250) + 100) - 1)
    assert s["atr"] == pytest.approx(4.0, rel=1e-3)        # high-low is 4 every day
    assert s["highs"][-1] == last + 2 and len(s["lows"]) == 20
    assert s["volume_change"] == pytest.approx(0.0)


def test_market_summary_with_a_short_history():
    s = pr.market_summary(_daily(n=60))
    assert s["sma200_dist"] is None and s["ret_90d"] is None
    assert s["sma50_dist"] is not None and s["volume_change"] is not None


def test_summary_lines_use_the_price_step():
    lines = pr.summary_lines(pr.market_summary(_daily(start=0.00001234, step=0.0)), 1e-8)
    assert lines[0] == "- Last price: 0.00001234 (price step 1e-08)"


@pytest.mark.parametrize("text,base,hit", [
    ("Ethereum developers set upgrade date", "ETH", True),
    ("Spot ether ETFs see inflows", "ETH", True),
    ("$ETH breaks out", "ETH", True),
    ("ETHFI token rallies", "ETH", False),
    ("Uniswap fee switch vote", "UNI", True),
    ("UNI rallies", "UNI", True),                    # an upper-case ticker of 3+ letters counts
    ("union news", "UNI", False),                    # lower case does not
    ("Bitcoin steady near 98k", "BTC", True),
    ("Solana ETF filing", "ETH", False),
])
def test_mentions(text, base, hit):
    assert pr.mentions(text, base) is hit


def test_short_tickers_need_their_name():
    assert pr.mentions("OP stack upgrade", "OP") is False
    assert pr.mentions("Optimism upgrade", "OP") is True


def _items(*titles):
    return [{"id": i, "source": "x", "published_at": "2026-11-09T16:19:45Z", "title": t, "summary": ""}
            for i, t in enumerate(titles, 1)]


def test_split_news_by_coin_and_general():
    per, general = pr.split_news(_items("Bitcoin up", "Ether ETFs", "Fed holds rates", "BTC and ETH both up"),
                                 ["BTC", "ETH"])
    assert [i["id"] for i in per["BTC"]] == [1, 4]
    assert [i["id"] for i in per["ETH"]] == [2, 4]
    assert [i["id"] for i in general] == [3]


def test_split_news_caps():
    per, general = pr.split_news(_items(*["Bitcoin"] * 30 + ["Fed"] * 30), ["BTC"])
    assert len(per["BTC"]) == pr.MAX_COIN_NEWS and len(general) == pr.MAX_GENERAL_NEWS


def _ctx():
    s = pr.market_summary(_daily())
    return pr.Context(s, fear_greed=[("2026-11-08", 57, "Greed")], btc_dominance=[("2026-11-08", 58.1)],
                      eth_dominance=[("2026-11-08", 11.5)])


def _sym(symbol, memo=""):
    return pr.SymbolInput(symbol, 0.01, pr.market_summary(_daily()), memo=memo)


def test_build_holds_every_symbol_in_order_with_its_news():
    news = _items("Ether ETFs see inflows", "Solana outage", "Fed holds rates")
    text = pr.build("2026-11-10 00:00:05", [_sym("ETH/USDT", memo="watch 3500"), _sym("SOL/USDT")], _ctx(), news)
    assert text.index("## Symbol: ETH/USDT") < text.index("## Symbol: SOL/USDT")
    assert "Call time: 2026-11-10 00:00:05 UTC (scheduled review)" in text
    assert "Fear & Greed: 57 (Greed)" in text
    assert "Your memo from the last call: 'watch 3500'" in text
    eth = text[text.index("## Symbol: ETH/USDT"):text.index("## Symbol: SOL/USDT")]
    assert '[1] 2026-11-09 16:19 UTC, x: "Ether ETFs see inflows"' in eth and "Solana outage" not in eth
    general = text[text.index("## General market headlines"):text.index("## Symbol")]
    assert "Fed holds rates" in general and "Ether ETFs" not in general
    assert '{"plans": [...]}' in text


def test_the_no_news_control_sees_no_headlines():
    text = pr.build("2026-11-10 00:00:05", [_sym("ETH/USDT")], _ctx(), news=None)
    assert "headlines" not in text.lower().replace("news_ids: ids of the headlines", "")


def test_the_rules_use_the_validator_limits():
    assert "0.5 to 4 daily ATRs" in pr.RULES and "at most 3 daily ATRs below" in pr.RULES
    assert "at most 300 characters" in pr.RULES
