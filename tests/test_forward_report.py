import pandas as pd
import pytest

import config
import forward_report
from engine import Position, Trade
from store import PaperStore


def test_report(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "INITIAL_EQUITY", 100.0)
    db = str(tmp_path / "paper.db")
    s = PaperStore(db)
    idx = pd.date_range("2026-01-01", periods=5, freq="1D")
    with s.candle():
        for t, a, b in zip(idx, [100, 110, 105, 120, 130], [100, 100, 100, 100, 100]):
            s.log_equity("run", "1d", "A", t, a)
            s.log_equity("run", "1d", "B", t, b)
        p = Position("t1", "LONG", 1.0, idx[0], 100.0, 0.1, 100.0, None)
        s.trade_opened("run", "1d", "A", p)
        s.trade_closed(Trade("t1", "A", "LONG", idx[0], idx[4], 100, 130, 1, 0.2, 29.8, 29.8, "SIGNAL"))

    closes = pd.DataFrame({"A": [10, 11, 10, 12, 13], "B": [10, 9, 8, 7, 6]}, index=idx, dtype=float)
    r = forward_report.report("run", "1d", db, closes)
    assert r["return_pct"] == pytest.approx(15.0)          # (130 + 100) / 200 - 1
    bh = (1 + closes.pct_change().mean(axis=1).fillna(0)).cumprod()
    assert r["buy_hold_pct"] == pytest.approx((bh.iloc[-1] - 1) * 100)
    assert r["closed_trades"] == 1 and not r["pass"]        # too short, too few trades
