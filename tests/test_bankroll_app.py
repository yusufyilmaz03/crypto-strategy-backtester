"""Bankroll instances in the panel, the notifier, Telegram and the watchers."""
import sqlite3

import pandas as pd
import pytest

from test_bankroll import DAYS, _check_books
from test_notify import DAY, Recorder
from kripto_bot import config, instances, settings
from kripto_bot.app import dashboard, notify, reporting, telegram_commands
from kripto_bot.trading import run_realtime, store as store_mod
from kripto_bot.trading.position_watch import ManualClose
from kripto_bot.trading.store import PaperStore

H = {"X-Requested-With": "kripto-bot"}
SETUP = {"name": "br", "strategy": "v9", "params": {"fast": 1, "slow": 50}, "timeframe": "1d",
         "symbols": list(DAYS), "equity": 1000, "sizing": "bankroll", "stakes": [0, 10, 20, 30],
         "score_inputs": ["trend_sma", "distance", "momentum"], "manual_close": True}


@pytest.fixture
def clock(monkeypatch):
    state = {"n": 300}
    monkeypatch.setattr(run_realtime, "fetch_recent",
                        lambda s, tf, limit=200, **kw: DAYS[s].iloc[:state["n"]].tail(limit).reset_index(drop=True))
    monkeypatch.setattr(run_realtime, "now_ms",
                        lambda: int(DAYS["AAA/USDT"].timestamp.iloc[state["n"] - 1].value // 10**6) + 86_400_000)
    return state


def _run(clock, inst, until=320):
    store = PaperStore()
    runners = [run_realtime.SymbolRunner(s, "1d", "v9", inst.engine_config(), store, params=inst.params,
                                         label=inst.run_key) for s in inst.symbol_list]
    br = run_realtime.BankrollRunner(runners, store, inst, rules={})
    for n in range(300, until):
        clock["n"] = n
        br.update()
    return store, br


@pytest.fixture
def client():
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def test_panel_shows_and_changes_the_bankroll(client, clock):
    r = client.post("/api/instances", json=SETUP, headers=H)
    assert r.status_code == 201 and r.get_json()["stakes"] == [0, 10, 20, 30]
    inst = instances.get("br")
    store, br = _run(clock, inst)
    view = client.get("/api/instances/br").get_json()
    b = view["summary"]["bankroll"]
    assert b["started"] and b["value"] == pytest.approx(br.balances()["value"]) and b["contributed"] == 1000
    assert b["pnl"] == pytest.approx(b["value"] - 1000) and b["low"] is False
    curves = client.get("/api/instances/br/curves").get_json()
    snaps = store.read("bankroll_snapshots")
    assert curves["strategy"][-1] == pytest.approx((snaps.twr.iloc[-1] - 1) * 100, abs=1e-3)
    assert curves["strategy"][0] == 0 and curves["buy_hold"][0] == 0
    for table in ["ledger", "requests", "snapshots", "positions", "trades", "signals"]:
        assert client.get(f"/api/instances/br/{table}").status_code == 200, table
    pos = client.get("/api/instances/br/positions").get_json()
    assert pos and all(p["score"] is not None and "score_now" in p for p in pos)
    assert all(s["score"] is not None for s in client.get("/api/instances/br/signals").get_json())

    # deposits and withdrawals are filed for the process (the instance is stopped: on start)
    r = client.post("/api/instances/br/bankroll", json={"kind": "deposit", "amount": 250, "note": "x"}, headers=H)
    assert r.status_code == 200 and r.get_json()["applied"] == "when the instance starts"
    r = client.post("/api/instances/br/bankroll", json={"kind": "withdraw", "amount": 1e6}, headers=H)
    assert r.status_code == 400 and "free" in r.get_json()["error"]
    assert client.post("/api/instances/br/bankroll", json={"kind": "deposit", "amount": -5}, headers=H).status_code == 400
    assert len(client.get("/api/instances/br/requests").get_json()) == 1
    assert client.get("/api/instances/br").get_json()["summary"]["bankroll"]["pending_requests"] == 1
    br.apply_requests()
    assert br.state["contributed"] == 1250

    # the low-bankroll threshold can change after creation; the stakes can't
    r = client.post("/api/instances/br/action", json={"action": "low", "low_basis": "free", "low_pct": 99}, headers=H)
    assert r.status_code == 200 and r.get_json()["low_basis"] == "free"
    assert client.get("/api/instances/br").get_json()["summary"]["bankroll"]["low"] in (True, False)

    instances.create({"name": "plain"})
    r = client.post("/api/instances/plain/bankroll", json={"kind": "deposit", "amount": 5}, headers=H)
    assert r.status_code == 400
    meta = client.get("/api/strategies").get_json()["bankroll"]
    assert meta["grades"][-1] == "Very strong" and "lower_tf" in meta["inputs"]
    for url in ["/", "/instances", "/instances/br"]:
        assert client.get(url).status_code == 200


def test_manual_close_returns_the_cash(clock):
    inst = instances.create(SETUP)
    store, br = _run(clock, inst)
    held = [r for r in br.runners if r.engine.position is not None]
    assert held
    r = held[0]
    free = br.state["free"]
    now = pd.Timestamp(r.last_time) + pd.Timedelta(hours=30)
    store.request_close(inst.run_key, "1d", r.symbol)
    ManualClose(br.runners, store, inst.run_key, "1d", price=lambda s: 150.0,
                fetch=lambda *a: pd.DataFrame(), clock=lambda: now).check()
    assert r.engine.position is None and r.engine.equity == 0
    assert br.state["free"] > free
    led = store.read("bankroll_ledger").iloc[-1]
    assert led.kind == "SELL" and "MANUAL" in led.note and led.symbol == r.symbol
    _check_books(store, br)
    saved = store.load_bankroll(inst.run_key, "1d")                       # persisted at once
    assert saved["free"] == pytest.approx(br.state["free"])


def test_low_bankroll_messages(tmp_path):
    path = str(tmp_path / "instances.json")
    inst = instances.create({**SETUP, "low_pct": 70}, path)
    rec = Recorder()
    w = notify.Watcher(rec, state_file=str(tmp_path / "state.json"), instances_path=path,
                       clock=lambda: DAY + 3600)              # before the daily summary hour
    store = PaperStore()

    def snap(value, n):
        b = {"free": value / 2, "reserved": 0.0, "invested": value / 2, "value": value, "contributed": 1000.0}
        store.log_snapshot(inst.run_key, "1d", pd.Timestamp("2026-01-01") + pd.Timedelta(days=n), b, value / 1000)
        store.conn.commit()

    snap(900, 0)
    w.poll(force=True)
    assert rec.messages == []
    snap(650, 1)
    w.poll(force=True)
    assert len(rec.messages) == 1 and "low bankroll" in rec.messages[0] and "65.0%" in rec.messages[0]
    snap(720, 2)                                       # above 70 but not 75: still low
    w.poll(force=True)
    assert len(rec.messages) == 1
    snap(800, 3)
    w.poll(force=True)
    assert len(rec.messages) == 2 and "recovered" in rec.messages[1]


def test_telegram_shows_bankroll_and_scores(clock):
    inst = instances.create(SETUP)
    store, br = _run(clock, inst)
    b = telegram_commands.CommandBot("token", "42", call=lambda *a, **k: True,
                                     prices=lambda symbols: {s: 110.0 for s in symbols}, log=lambda *a: None)
    text, _ = b._instance(inst)
    assert "Bankroll" in text and "Contributed" in text and "stakes 0/10/20/30%" in text
    text, _ = b._positions(inst)
    assert "Sc" in text
    text, _ = b._signals(inst)
    assert "Sc" in text
    assert telegram_commands._action("ignored (not funded: no free cash; Normal 45)") == "no cash"
    assert telegram_commands._action("rotation: close at next open for X") == "rotate"


def test_databases_without_score_columns(tmp_path):
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE trades (trade_id TEXT PRIMARY KEY, symbol TEXT, strategy TEXT, timeframe TEXT, side TEXT,
            entry_time TEXT, exit_time TEXT, entry_price REAL, exit_price REAL, qty REAL, fees REAL, pnl REAL,
            return_pct REAL, reason TEXT, entry_rsi REAL, stop REAL);
        CREATE TABLE signals (id INTEGER PRIMARY KEY AUTOINCREMENT, candle_time TEXT, symbol TEXT, strategy TEXT,
            timeframe TEXT, signal TEXT, rsi REAL, close REAL, position TEXT, action TEXT);
        INSERT INTO trades (trade_id, symbol, strategy, timeframe, side, entry_time, entry_price, qty)
            VALUES ('t', 'X/USDT', 'instance:old', '1d', 'LONG', '2026-01-01', 1.0, 2.0);
        INSERT INTO signals (candle_time, symbol, strategy, timeframe, signal, action)
            VALUES ('2026-01-01', 'X/USDT', 'instance:old', '1d', 'BUY', 'x');""")
    conn.commit()
    conn.close()
    inst = instances.Instance(name="old", run_key="instance:old")
    assert len(reporting.positions(inst, db)) == 1                     # the panel reads it before any migration
    assert len(reporting.signals(inst, paper_db=db)) == 1
    PaperStore(db)                                                     # a bot opens it: the columns are added
    cols = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(trades)")]
    assert "score" in cols and "grade" in cols
