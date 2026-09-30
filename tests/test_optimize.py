import pandas as pd
import pytest

import strategy
from conftest import make_ohlcv
from engine import EngineConfig
from evaluation import walk_forward_splits
from indicators import add_indicators
from optimize import _chain, walk_forward_optimize


@pytest.fixture(scope="module")
def df():
    return add_indicators(make_ohlcv(3000, seed=21, freq="1h"))


def test_wfo_structure(df):
    res = walk_forward_optimize(df, "v6", "1h", EngineConfig(), n_folds=4, min_trades=3)
    splits = walk_forward_splits(len(df), 4, 0.4, False)
    assert len(res["folds"]) == 4
    assert len(res["oos_equity"]) == sum(len(t) for _, t in splits)
    assert res["n_trials"] == 4 * len(strategy.param_combinations("v6")) * 3
    for f in res["folds"]:
        if f["params"] is not None:
            assert f["params"]["stop"] in (1.5, 2.0, 3.0)
            grid_part = {k: v for k, v in f["params"].items() if k != "stop"}
            assert grid_part in strategy.param_combinations("v6")
    # OOS trades only happen inside test windows
    first_test = df["timestamp"].iloc[splits[0][1].start]
    assert (res["oos_trades"]["entry_time"] >= first_test).all()


def test_wfo_stays_flat_when_nothing_qualifies(df):
    res = walk_forward_optimize(df, "v1", "1h", EngineConfig(), n_folds=3, min_trades=10_000)
    assert all(f["params"] is None for f in res["folds"])
    assert res["oos"]["return_pct"] == 0 and res["oos"]["trades"] == 0


def test_wfo_choice_ignores_data_after_training_window(df):
    """Fold 1's parameter choice may only depend on its training window."""
    base = walk_forward_optimize(df, "v4", "1h", EngineConfig(), n_folds=4, min_trades=3)
    train_end = walk_forward_splits(len(df), 4, 0.4, False)[0][1].start
    shocked = df.copy()
    shocked.loc[train_end:, ["open", "high", "low", "close"]] *= 0.5
    shocked = add_indicators(shocked[["timestamp", "open", "high", "low", "close", "volume"]].copy())
    other = walk_forward_optimize(shocked, "v4", "1h", EngineConfig(), n_folds=4, min_trades=3)
    assert base["folds"][0]["params"] == other["folds"][0]["params"]


def test_chain_compounds_windows():
    a = pd.Series([1000.0, 1100.0])
    b = pd.Series([1000.0, 900.0])
    out = _chain([a, b], 1000.0)
    assert out.iloc[-1] == pytest.approx(1000 * 1.1 * 0.9)


def test_higher_costs_do_not_help(df):
    res = walk_forward_optimize(df, "v5", "1h", EngineConfig(), n_folds=3, min_trades=3)
    assert res["high_cost_return_pct"] <= res["oos"]["return_pct"] + 1e-9
