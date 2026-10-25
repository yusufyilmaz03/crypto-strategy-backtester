import json

import pandas as pd
import pytest

from kripto_bot import config
from kripto_bot.app import dashboard
from kripto_bot import instances
from kripto_bot.app import playground
from kripto_bot.app import reporting
from kripto_bot.trading import run_realtime
from kripto_bot import settings
from conftest import make_ohlcv
from kripto_bot.core.engine import EngineConfig
from kripto_bot.trading.store import PaperStore

FULL = make_ohlcv(700, seed=3)
H = {"X-Requested-With": "kripto-bot"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(instances, "INSTANCES_FILE", str(tmp_path / "instances.json"))
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(playground, "PLAYGROUND_DB", str(tmp_path / "pg.db"))
    monkeypatch.setattr(reporting, "STATUS_FILE", str(tmp_path / "status.json"))
    monkeypatch.setattr(reporting, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(config, "KILL_SWITCH_FILE", str(tmp_path / "KILL_SWITCH"))
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def _post(c, url, body=None):
    return c.post(url, json=body or {}, headers=H)


def test_pages_render(client):
    for url in ["/", "/instances", "/instances/forward-v9", "/playground", "/settings", "/backtest", "/health"]:
        assert client.get(url).status_code == 200, url
    assert client.get("/instances/nope").status_code == 400


def test_mutations_require_csrf_header(client):
    assert client.post("/api/kill-switch", json={"mode": "block"}).status_code == 403
    assert client.delete("/api/instances/forward-v9").status_code == 403


def test_instance_lifecycle(client):
    r = _post(client, "/api/instances", {"name": "trend", "strategy": "v10", "timeframe": "4h",
                                         "params": {"entry": "55"}, "symbols": ["btc/usdt"]})
    assert r.status_code == 201 and r.get_json()["params"] == {"entry": 55}
    assert _post(client, "/api/instances", {"name": "trend"}).status_code == 400          # duplicate
    assert _post(client, "/api/instances/trend/action", {"action": "start"}).get_json()["enabled"]
    assert client.delete("/api/instances/trend", headers=H).status_code == 400             # running
    _post(client, "/api/instances/trend/action", {"action": "stop"})
    _post(client, "/api/instances/trend/action", {"action": "describe", "description": "hi"})
    assert client.get("/api/instances/trend").get_json()["description"] == "hi"
    assert client.delete("/api/instances/trend", headers=H).status_code == 200
    names = [i["name"] for i in client.get("/api/instances").get_json()]
    assert names == ["forward-v9", "testnet-v9"]


def test_instance_data_endpoints(client, tmp_path, monkeypatch):
    inst = instances.create({"name": "demo", "strategy": "v5", "timeframe": "5m", "stop": "loose",
                             "symbols": ["X/USDT"], "equity": 1000})
    state = {"n": 300}
    monkeypatch.setattr(run_realtime, "fetch_recent",
                        lambda s, tf, limit=200, **kw: FULL.iloc[:state["n"]].tail(limit).reset_index(drop=True))
    monkeypatch.setattr(run_realtime, "now_ms",
                        lambda: int(FULL.timestamp.iloc[state["n"] - 1].value // 10**6) + 60_000)
    runner = run_realtime.SymbolRunner("X/USDT", "5m", "v5", inst.engine_config(), PaperStore(),
                                       label=inst.run_key)
    runner.update()
    while state["n"] < len(FULL):
        state["n"] += 1
        runner.update()

    curves = client.get("/api/instances/demo/curves").get_json()
    marked = runner.engine.marked_equity(FULL.close.iloc[-1])
    assert curves["strategy"][-1] == pytest.approx((marked / 1000 - 1) * 100, abs=1e-3)
    closes = FULL.close.iloc[299:]
    assert curves["buy_hold"][-1] == pytest.approx((closes.iloc[-1] / closes.iloc[1] - 1) * 100, abs=1e-3)
    summary = client.get("/api/instances/demo").get_json()["summary"]
    assert summary["closed_trades"] == len(runner.engine.trades)
    for table in ["positions", "trades", "signals", "log"]:
        assert client.get(f"/api/instances/demo/{table}").status_code == 200
    json.dumps(client.get("/api/overview").get_json())


def test_settings_api(client):
    v = client.get("/api/settings").get_json()
    assert "Costs" in v["groups"] and set(v["secrets"]) == {"Binance API key", "Testnet API key", "Telegram bot"}
    r = _post(client, "/api/settings", {"changes": {"MAX_ORDER_USDT": 42}})
    assert r.status_code == 200 and r.get_json()["restart"] == ["testnet"]
    assert config.MAX_ORDER_USDT == 42.0
    assert _post(client, "/api/settings", {"changes": {"LIVE_TRADING_ENABLED": True}}).status_code == 400
    assert _post(client, "/api/settings", {"changes": {"FEE_RATE_TAKER": 9}}).status_code == 400
    _post(client, "/api/settings", {"reset": ["MAX_ORDER_USDT"]})
    assert config.MAX_ORDER_USDT == 100.0


def test_settings_restart_marks_running_instances(client):
    r = _post(client, "/api/settings/restart", {"services": ["paper"]}).get_json()
    assert r["restarted"] == ["forward-v9"]
    assert instances.get("forward-v9").restart_requested_at


def test_kill_switch(client):
    assert _post(client, "/api/kill-switch", {"mode": "flatten"}).get_json()["kill_switch"] == "flatten"
    assert client.get("/api/overview").get_json()["kill_switch"] == "flatten"
    assert _post(client, "/api/kill-switch", {"mode": "off"}).get_json()["kill_switch"] == "off"
    assert _post(client, "/api/kill-switch", {"mode": "boom"}).status_code == 400


def test_playground_api(client, monkeypatch):
    df = make_ohlcv(800, seed=5, freq="1D", start="2024-01-01")
    monkeypatch.setattr(playground, "load_ohlcv", lambda s, tf, days: df)
    r = _post(client, "/api/playground/run", {"symbol": "BTC/USDT", "timeframe": "1d", "strategy": "v9",
                                              "params": {"slow": 50}, "days": 200})
    assert r.status_code == 200, r.get_json()
    assert len(r.get_json()["candles"]) == 200
    assert client.get("/api/playground/history").get_json()["total"] == 1
    bad = _post(client, "/api/playground/run", {"symbol": "BTC/USDT", "timeframe": "1d", "strategy": "v99"})
    assert bad.status_code == 400 and "unknown strategy" in bad.get_json()["error"]


def test_strategies_api(client):
    d = client.get("/api/strategies").get_json()
    keys = [s["key"] for s in d["strategies"]]
    assert "v9" in keys and d["stops"]["none"] is None
    v9 = next(s for s in d["strategies"] if s["key"] == "v9")
    assert {p["name"] for p in v9["params"]} == {"fast", "slow", "entry_band"} and v9["description"]


def test_password_protects_every_request(client, monkeypatch):
    import base64
    monkeypatch.setenv("DASHBOARD_PASSWORD", "s3cret")
    assert client.get("/").status_code == 401
    assert client.get("/api/overview").status_code == 401
    good = {"Authorization": "Basic " + base64.b64encode(b"me:s3cret").decode()}
    bad = {"Authorization": "Basic " + base64.b64encode(b"me:nope").decode()}
    assert client.get("/", headers=bad).status_code == 401
    assert client.get("/", headers=good).status_code == 200
    assert client.post("/api/kill-switch", json={"mode": "off"}, headers=good).status_code == 403  # CSRF still on


def test_network_bind_requires_password(monkeypatch):
    monkeypatch.setenv("DASHBOARD_HOST", "0.0.0.0")
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    with pytest.raises(SystemExit, match="DASHBOARD_PASSWORD"):
        dashboard.bind_host()
    monkeypatch.setenv("DASHBOARD_PASSWORD", "x")
    assert dashboard.bind_host() == "0.0.0.0"
    monkeypatch.delenv("DASHBOARD_HOST")
    assert dashboard.bind_host() == "127.0.0.1"
