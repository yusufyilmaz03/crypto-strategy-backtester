import numpy as np
import pandas as pd
import pytest

from conftest import make_ohlcv
from rotation import build_panel, rotation_combinations, simulate


def panel(prices):
    """prices: {symbol: list of prices}; open == close == price for simplicity."""
    idx = pd.date_range("2025-01-01", periods=len(next(iter(prices.values()))), freq="1D")
    df = pd.DataFrame(prices, index=idx, dtype=float)
    return df.copy(), df.copy()


def test_holds_the_winner_and_skips_the_loser():
    opens, closes = panel({"UP": [100, 101, 102, 104, 108, 116], "DOWN": [100, 99, 98, 97, 96, 95]})
    res = simulate(opens, closes, range(1, 6), lookback=1, top_k=1, rebalance=1,
                   fee_rate=0.0, slippage_bps=0.0, initial_equity=1000)
    # decision at close of row 1 (UP +1%), bought at the open of row 2 (102) and held
    assert res.equity.iloc[0] == 1000
    assert res.equity.iloc[-1] == pytest.approx(1000 * 116 / 102)
    assert res.trades == 1


def test_stays_in_cash_without_positive_momentum():
    opens, closes = panel({"A": [100, 99, 98, 97], "B": [50, 49, 48, 47]})
    res = simulate(opens, closes, range(1, 4), lookback=1, top_k=2, rebalance=1, fee_rate=0.001)
    assert (res.equity == 1000).all() and res.trades == 0


def test_partial_allocation_when_fewer_winners_than_k():
    opens, closes = panel({"A": [100, 110, 110, 121], "B": [100, 90, 90, 90]})
    res = simulate(opens, closes, range(1, 4), lookback=1, top_k=2, rebalance=1,
                   fee_rate=0.0, slippage_bps=0.0)
    # half the equity goes into A at 110, which ends at 121 (+10%); the other half stays in cash
    assert res.equity.iloc[-1] == pytest.approx(500 + 500 * 1.1)


def test_costs_on_turnover():
    opens, closes = panel({"A": [100, 101, 101, 101]})
    res = simulate(opens, closes, range(1, 4), lookback=1, top_k=1, rebalance=10,
                   fee_rate=0.001, slippage_bps=10.0)
    assert res.fees == pytest.approx(1000 * 0.002)
    assert res.equity.iloc[-1] == pytest.approx(1000 - 2.0)


def test_no_lookahead():
    frames = {f"S{i}": make_ohlcv(300, seed=i, freq="1D") for i in range(6)}
    opens, closes = build_panel(frames)
    base = simulate(opens, closes, range(130, 300), 60, 3, 7).equity
    cut = 200
    o2, c2 = opens.copy(), closes.copy()
    o2.iloc[cut + 1:] *= np.linspace(0.5, 2, 6)
    c2.iloc[cut + 1:] *= np.linspace(0.5, 2, 6)
    changed = simulate(o2, c2, range(130, 300), 60, 3, 7).equity
    assert base.loc[:closes.index[cut]].equals(changed.loc[:closes.index[cut]])


def test_grid():
    assert len(rotation_combinations()) == 12


def test_walk_forward_rotation_structure():
    from engine import EngineConfig
    from rotation import walk_forward_rotation

    frames = {f"S{i}": make_ohlcv(900, seed=i, freq="1D") for i in range(8)}
    opens, closes = build_panel(frames)
    res = walk_forward_rotation(opens, closes, range(0, 900), "1d", EngineConfig(),
                                n_folds=4, min_trades=1)
    assert len(res["folds"]) == 4
    assert res["n_trials"] == 4 * 12
    assert len(res["oos_equity"]) == 900 - 360
    assert res["high_cost_equity"].iloc[-1] <= res["oos_equity"].iloc[-1] + 1e-9
