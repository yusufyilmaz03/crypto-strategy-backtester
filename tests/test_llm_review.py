import json

import numpy as np
import pandas as pd
import pytest

from kripto_bot.core import market_context as mc
from kripto_bot.llm import inputs, review
from kripto_bot.llm import prompt as pr
from kripto_bot.llm import provider as p
from kripto_bot.llm.store import LLMStore

T = pd.Timestamp("2026-11-10 00:00:05", tz="UTC")


def _daily(last):
    close = np.linspace(last * 0.8, last, 250)
    return pd.DataFrame({"timestamp": pd.date_range("2026-03-06", periods=250, freq="D"), "open": close,
                         "high": close * 1.02, "low": close * 0.98, "close": close, "volume": np.full(250, 5.0)})


def snap(symbols=("ETH/USDT", "SOL/USDT"), news=()):
    syms = {s: pr.SymbolInput(s, 0.01, pr.market_summary(_daily(3000.0 if s == "ETH/USDT" else 150.0)))
            for s in symbols}
    return inputs.Snapshot(T, pr.Context(pr.market_summary(_daily(98000.0))), syms, list(news))


def hold(symbol):
    return {"symbol": symbol, "action": "hold", "entry": None, "stop": None, "take_profit": None, "p_tp": None,
            "grade": None, "review_if": [], "reasons": ["wait"], "news_ids": [], "memo": ""}


class Answers(p.Provider):
    """Returns queued results; each is an LLMResult or a list of plans for an OK answer."""

    def __init__(self, *results):
        self.results, self.prompts = list(results), []

    def complete(self, prompt, schema):
        self.prompts.append(prompt)
        r = self.results.pop(0)
        if isinstance(r, p.LLMResult):
            return r
        return p.LLMResult(p.OK, output={"plans": r}, raw=json.dumps({"plans": r}), model="m", effort="high",
                           usage={"input_tokens": 100, "output_tokens": 10, "reasoning_output_tokens": 4})


def _run(provider, tmp_path, symbols=("ETH/USDT", "SOL/USDT"), **kw):
    s = snap(symbols)
    st = LLMStore(str(tmp_path / "paper.db"))
    ordered = [s.symbols[x] for x in symbols]
    ctxs = {x.symbol: inputs.validator_context(x) for x in ordered}
    return review.run(provider, st, "v11-test", "group", s, ordered, ctxs, sleep=lambda _: None, **kw), st


def test_every_plan_is_validated_and_stored(tmp_path):
    bad = dict(hold("SOL/USDT"), memo="x" * 400)
    out, st = _run(Answers([hold("ETH/USDT"), bad]), tmp_path)
    assert out.call_kind == p.OK
    assert out.verdicts["ETH/USDT"].ok and not out.verdicts["SOL/USDT"].ok
    plans = st.plans("v11-test")
    assert list(plans["symbol"]) == ["ETH/USDT", "SOL/USDT"] and list(plans["accepted"]) == [1, 0]
    assert "memo" in json.loads(plans["reasons"].iloc[1])[0]
    assert json.loads(plans["context"].iloc[0])["atr"] > 0
    call = st.calls("v11-test").iloc[0]
    assert json.loads(call["symbols"]) == ["ETH/USDT", "SOL/USDT"]
    assert call["input_tokens"] == 100 and call["reasoning_tokens"] == 4 and "## Symbol: SOL/USDT" in call["prompt"]


def test_a_missing_plan_is_a_rejection(tmp_path):
    out, st = _run(Answers([hold("ETH/USDT")]), tmp_path)
    assert out.verdicts["SOL/USDT"].reasons == ["no plan for this symbol"]


def test_a_failed_call_rejects_every_symbol_and_both_attempts_are_stored(tmp_path):
    out, st = _run(Answers(p.LLMResult(p.TIMEOUT), p.LLMResult(p.TIMEOUT)), tmp_path)
    assert out.call_kind == p.TIMEOUT
    assert all(v.reasons == ["call failed: timeout"] for v in out.verdicts.values())
    calls = st.calls("v11-test")
    assert list(calls["retry"]) == [0, 1] and list(calls["outcome"]) == ["timeout", "timeout"]
    assert st.plans("v11-test").empty


def test_the_no_news_control_gets_no_headlines(tmp_path):
    provider = Answers([hold("ETH/USDT"), hold("SOL/USDT")])
    _run(provider, tmp_path, news=False)
    assert "Headlines mentioning" not in provider.prompts[0]


def test_market_context_only_uses_values_fetched_before_the_call(tmp_path):
    path = str(tmp_path / "paper.db")
    ctx = mc.MarketContext(path)
    rows = [(f"2026-11-0{d}", "fear_greed", "value", 50 + d, "Neutral", f"2026-11-0{d}T00:10:00+00:00") for d in range(1, 10)]
    rows += [("2026-11-10", "fear_greed", "value", 99, "Greed", "2026-11-10T00:10:00+00:00")]     # after the call
    rows += [("2026-11-09", "coingecko_global", "btc_dominance", 58.1, None, "2026-11-09T00:05:00+00:00")]
    ctx._insert(rows)
    ctx.close()
    fg, btc, eth = inputs.market_context(T, path)
    assert [v for _, v, _ in fg] == [53, 54, 55, 56, 57, 58, 59] and btc == [("2026-11-09", 58.1)] and eth == []
