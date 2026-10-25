from dataclasses import replace

import numpy as np
import pandas as pd

from kripto_bot import config
from kripto_bot.analysis import exits
from kripto_bot.analysis import survivorship as sv


def walk(seed, n=400, start="2020-09-01"):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.04, n)))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"timestamp": pd.date_range(start, periods=n, freq="D"), "open": open_,
                         "high": np.maximum(open_, close) * 1.01, "low": np.minimum(open_, close) * 0.99,
                         "close": close, "volume": 1.0, "quote_volume": rng.uniform(1, 10, n)})


def test_run_reports_every_variant_and_the_criteria(monkeypatch):
    monkeypatch.setattr(sv, "PERIODS", {"B": ("2021-01-01", "2021-04-01"), "A": ("2021-04-01", "2021-07-01")})
    assets = {f"C{i}USDT": walk(i) for i in range(5)}
    table, curves = exits.run(assets, n=3, workers=1, log=lambda *a: None)
    names = set(exits.variants())
    for p in ("B", "A"):
        got = table[table.period == p]
        assert set(got[got.costs == "base"].variant) == names
        assert set(got[got.costs == "x1.5"].variant) == {exits.BASELINE, *exits.PRIMARY.values()}
    stressed = table[(table.variant == exits.BASELINE) & (table.period == "B")].set_index("costs")
    assert stressed.loc["x1.5", "return_pct"] <= stressed.loc["base", "return_pct"]
    checks = exits.verdict(table)
    assert set(checks) == {"T", "H"} and all(len(c) == 6 for c in checks.values())


def test_a_trailing_exit_too_wide_to_trigger_matches_the_baseline():
    cfg = replace(config.engine_config(), atr_multiplier=None)
    df = walk(3)
    _, wide = exits._job(("X", df, "wide", "base", {}, 1000.0, cfg))
    _, base = exits._job(("X", df, "base", "base", {}, None, cfg))
    pd.testing.assert_series_equal(wide[0], base[0])
    _, tight = exits._job(("X", df, "tight", "base", {}, 1.0, cfg))
    assert not tight[0].equals(base[0])
