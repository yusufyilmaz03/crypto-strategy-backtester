import json

import pytest

import config
import dashboard
import run_realtime
from conftest import make_ohlcv
from engine import EngineConfig
from store import PaperStore

FULL = make_ohlcv(700, seed=3)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "strategy_label", lambda: "v5")
    monkeypatch.setattr(config, "TIMEFRAME", "5m")
    monkeypatch.setattr(config, "SYMBOLS", ["X/USDT", "Y/USDT"])
    state = {"n": 300}
    monkeypatch.setattr(run_realtime, "fetch_recent",
                        lambda s, tf, limit=200, **kw: FULL.iloc[:state["n"]].tail(limit).reset_index(drop=True))
    monkeypatch.setattr(run_realtime, "now_ms",
                        lambda: int(FULL.timestamp.iloc[state["n"] - 1].value // 10**6) + 60_000)
    runner = run_realtime.SymbolRunner("X/USDT", "5m", "v5", EngineConfig(), PaperStore())
    runner.update()
    while state["n"] < len(FULL):
        state["n"] += 1
        runner.update()
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client(), runner


def test_routes(client):
    c, _ = client
    for url in ["/", "/signals", "/trades", "/pnl_data", "/equity_data", "/last_trade",
                "/dist_data?metric=winloss", "/dist_data?metric=pnl_by_symbol", "/backtest",
                "/api/backtest", "/health"]:
        assert c.get(url).status_code == 200, url


def test_equity_matches_engine(client):
    c, runner = client
    data = json.loads(c.get("/equity_data").data)
    # Portfolio = traded symbol's equity + one idle symbol at its initial equity.
    marked = runner.engine.marked_equity(FULL.close.iloc[-1])
    expected = (marked + config.INITIAL_EQUITY) / (2 * config.INITIAL_EQUITY) * 100 - 100
    assert data["return_pct"][-1] == pytest.approx(expected, abs=1e-3)
    pnl = json.loads(c.get("/pnl_data").data)
    assert pnl["cum_pnl"][-1] == pytest.approx(sum(t.pnl for t in runner.engine.trades))


def test_empty_database(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = dashboard.app.test_client()
    for url in ["/", "/signals", "/trades", "/pnl_data", "/equity_data", "/last_trade"]:
        assert c.get(url).status_code == 200, url


def test_buy_and_hold_line(client):
    c, _ = client
    data = json.loads(c.get("/equity_data").data)
    closes = FULL.close.iloc[299:]   # candles fed to the engine (first one only sets the start)
    expected = (closes.iloc[-1] / closes.iloc[1] - 1) * 100
    assert data["buy_hold_pct"][-1] == pytest.approx(expected, abs=1e-3)  # endpoint rounds to 4 decimals
    assert len(data["buy_hold_pct"]) == len(data["return_pct"])


def test_store_adds_close_column_to_old_databases(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE equity (symbol TEXT, strategy TEXT, timeframe TEXT, time TEXT, "
                     "equity REAL, PRIMARY KEY (symbol, strategy, timeframe, time))")
    s = PaperStore(str(db))
    with s.candle():
        s.log_equity("r", "1d", "A", "2026-01-01", 1000.0, 10.0)
    assert s.read("equity")["close"].tolist() == [10.0]
