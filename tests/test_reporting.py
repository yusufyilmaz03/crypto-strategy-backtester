import json

import pandas as pd
import pytest

from kripto_bot import instances
from kripto_bot.app import reporting
from kripto_bot.core.engine import Position, Trade
from kripto_bot.trading.store import PaperStore


@pytest.fixture
def inst(tmp_path):
    path = str(tmp_path / "instances.json")
    i = instances.create({"name": "demo", "symbols": ["A/USDT", "B/USDT"], "equity": 100}, path)
    db = str(tmp_path / "paper.db")
    s = PaperStore(db)
    idx = pd.date_range("2026-01-01", periods=4, freq="1D")
    with s.candle():
        for t, a, ca, cb in zip(idx, [100, 110, 99, 120], [10, 11, 9.9, 12], [5, 5, 5, 5.5]):
            s.log_equity(i.run_key, "1d", "A/USDT", t, a, ca)
            s.log_equity(i.run_key, "1d", "B/USDT", t, 100, cb)
        p = Position("t1", "LONG", 1.0, idx[0], 100.0, 0.1, 100.0, None)
        s.trade_opened(i.run_key, "1d", "A/USDT", p)
        s.trade_closed(Trade("t1", "A/USDT", "LONG", idx[0], idx[3], 100, 120, 1, 0.2, 19.8, 19.8, "SIGNAL"))
    return i, db


def test_curves_and_summary(inst):
    i, db = inst
    strat, bh = reporting.curves(i, db)
    assert strat.iloc[-1] == pytest.approx((120 + 100) / 200 * 100 - 100)
    exp_bh = ((1 + pd.DataFrame({"a": [10, 11, 9.9, 12], "b": [5, 5, 5, 5.5]}).pct_change().mean(axis=1)
               .fillna(0)).cumprod().iloc[-1] - 1) * 100
    assert bh.iloc[-1] == pytest.approx(exp_bh)
    s = reporting.summary(i, db)
    assert s["closed_trades"] == 1 and s["win_rate_pct"] == 100.0 and s["open_positions"] == 0
    assert s["max_dd_pct"] == pytest.approx((105 - 99.5) / 105 * 100)
    json.dumps(s)


def test_service_status(tmp_path):
    f = tmp_path / "status.json"
    assert reporting.service_status("x", str(f))["state"] == "supervisor not running"
    f.write_text(json.dumps({"updated_at": pd.Timestamp.now(tz="UTC").isoformat(),
                             "services": {"x": {"state": "running", "pid": 1}}, "blocked": {"y": "no keys"}}))
    assert reporting.service_status("x", str(f))["state"] == "running"
    assert reporting.service_status("y", str(f))["state"] == "blocked"
    assert reporting.service_status("z", str(f))["state"] == "stopped"


def test_log_tail(tmp_path):
    (tmp_path / "demo.log").write_text("\n".join(f"line {k}" for k in range(10)) + "\n")
    assert reporting.log_tail("demo", 3, str(tmp_path)) == ["line 7", "line 8", "line 9"]


def test_log_tail_reads_from_the_end_across_blocks(tmp_path):
    (tmp_path / "big.log").write_text("".join(f"line {k}\n" for k in range(5000)))
    tail = reporting.log_tail("big", 300, str(tmp_path), block=100)
    assert tail == [f"line {k}" for k in range(4700, 5000)]
    assert reporting.log_tail("big", 10000, str(tmp_path), block=100) == [f"line {k}" for k in range(5000)]
    (tmp_path / "short.log").write_text("a\nb")
    assert reporting.log_tail("short", 5, str(tmp_path)) == ["a", "b"]


def test_buy_and_hold_with_gaps_matches_the_old_padding_without_warning():
    import warnings
    closes = pd.DataFrame({"a": [10, None, 12, 11], "b": [5, 6, None, 7.0]})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        old = closes.pct_change()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        new = closes.ffill().pct_change(fill_method=None)
    pd.testing.assert_frame_equal(old, new)
