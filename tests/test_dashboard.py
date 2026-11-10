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


TAILSCALE = {"base_url": "http://mac.tail1.ts.net", "environ_base": {"REMOTE_ADDR": "127.0.0.1"},
             "headers": {"X-Forwarded-For": "100.64.0.2", "X-Forwarded-Host": "mac.tail1.ts.net",
                         "X-Forwarded-Proto": "https"}}


def test_password_protects_every_remote_request(client, monkeypatch):
    import base64
    monkeypatch.setenv("DASHBOARD_PASSWORD", "s3cret")
    lan = {"base_url": "http://192.168.1.5:5002", "environ_base": {"REMOTE_ADDR": "192.168.1.9"}}
    assert client.get("/", **lan).status_code == 401
    assert client.get("/api/overview", **lan).status_code == 401
    good = "Basic " + base64.b64encode(b"me:s3cret").decode()
    bad = "Basic " + base64.b64encode(b"me:nope").decode()
    assert client.get("/", headers={"Authorization": bad}, **lan).status_code == 401
    assert client.get("/", headers={"Authorization": good}, **lan).status_code == 200
    assert client.post("/api/kill-switch", json={"mode": "off"}, headers={"Authorization": good},
                       **lan).status_code == 403                                     # CSRF still on


def test_password_is_not_asked_on_this_computer_only(client, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "s3cret")
    for url in ("http://127.0.0.1:5002", "http://localhost:5002"):
        assert client.get("/", base_url=url, environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200
    assert client.get("/", base_url="http://[::1]:5002", environ_base={"REMOTE_ADDR": "::1"}).status_code == 200
    # tailscale serve connects from 127.0.0.1 too, but with forwarding headers and its host name
    assert client.get("/", **TAILSCALE).status_code == 401
    only_header = {"X-Forwarded-For": "100.64.0.2"}
    assert client.get("/", base_url="http://127.0.0.1:5002", headers=only_header).status_code == 401
    # a web page whose name resolves to 127.0.0.1 (DNS rebinding) keeps its own host name
    assert client.get("/", base_url="http://evil.example:5002",
                      environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 401


def test_network_bind_requires_password(monkeypatch):
    monkeypatch.setenv("DASHBOARD_HOST", "0.0.0.0")
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    with pytest.raises(SystemExit, match="DASHBOARD_PASSWORD"):
        dashboard.bind_host()
    monkeypatch.setenv("DASHBOARD_PASSWORD", "x")
    assert dashboard.bind_host() == "0.0.0.0"
    monkeypatch.delenv("DASHBOARD_HOST")
    assert dashboard.bind_host() == "127.0.0.1"


def test_access_log_drops_successful_requests_only():
    import logging
    from kripto_bot.app.dashboard import _SkipSuccessfulRequests
    f = _SkipSuccessfulRequests()

    def rec(msg, args):
        return logging.LogRecord("werkzeug", logging.INFO, "", 0, msg, args, None)
    fmt = '127.0.0.1 - - [x] "%s" %s %s\n'
    assert not f.filter(rec(fmt, ("GET /api/overview HTTP/1.1", "200", "-")))
    assert not f.filter(rec(fmt, ("GET /static/app.js HTTP/1.1", "304", "-")))
    assert f.filter(rec(fmt, ("GET /nope HTTP/1.1", "404", "-")))
    assert f.filter(rec(fmt, ("POST /api/x HTTP/1.1", "500", "-")))
    assert f.filter(rec(" * Running on %s", ("http://127.0.0.1:5002",)))
    assert f.filter(rec("plain message", None))


def test_favorite_can_change_any_time(client):
    _post(client, "/api/instances", {"name": "fav-me", "symbols": ["X/USDT"]})
    r = _post(client, "/api/instances/fav-me/action", {"action": "favorite", "favorite": True})
    assert r.status_code == 200 and r.get_json()["favorite"] is True
    assert instances.get("fav-me").favorite is True
    assert _post(client, "/api/instances/fav-me/action", {"action": "favorite", "favorite": False}).get_json()["favorite"] is False
    assert instances.get("fav-me").enabled is False            # nothing else changed


def test_start_clears_the_self_stop_reason(client):
    _post(client, "/api/instances", {"name": "wound-down"})
    instances.update("wound-down", stopped_reason="exit only and no open positions", stopped_at="2026-11-08T10:00:00")
    assert client.get("/api/instances/wound-down").get_json()["stopped_reason"]
    r = _post(client, "/api/instances/wound-down/action", {"action": "start"}).get_json()
    assert r["enabled"] and r["stopped_reason"] is None and r["stopped_at"] is None and r["exit_only"] is False


def test_a_v11_instance_shows_like_a_paper_instance(client):
    instances.create({"name": "v11-astra-high", "strategy": "v11", "timeframe": "1d",
                      "symbols": ["ETH/USDT", "SOL/USDT"], "params": {"model": "gpt-6-astra", "effort": "high"}})
    assert client.get("/instances/v11-astra-high").status_code == 200
    assert client.get("/").status_code == 200 and client.get("/instances").status_code == 200
    detail = client.get("/api/instances/v11-astra-high").get_json()
    assert detail["strategy"] == "v11" and detail["params"]["model"] == "gpt-6-astra"
    assert client.get("/api/instances/v11-astra-high/curves").status_code == 200
    assert "v11-astra-high" in [i["name"] for i in client.get("/api/instances").get_json()]
    for table in ("trades", "positions", "signals"):
        assert client.get(f"/api/instances/v11-astra-high/{table}").status_code in (200, 400), table


def test_the_form_offers_v11_and_creates_it_like_the_browser_does(client, monkeypatch):
    from kripto_bot.core import universe
    monkeypatch.setattr(universe, "saved", lambda: [{"file": "universe_2026-11-09.json", "date": "2026-11-09",
                                                    "symbols": ["BTC/USDT", "ETH/USDT"]}])
    meta = client.get("/api/strategies").get_json()
    v11 = next(s for s in meta["strategies"] if s["key"] == "v11")
    assert v11["analyst"] and [p["name"] for p in v11["params"]] == ["model", "effort", "news", "group"]
    assert meta["universes"][0]["symbols"] == ["BTC/USDT", "ETH/USDT"]
    body = {"name": "v11-nonews", "mode": "paper", "strategy": "v11", "timeframe": "1d", "stop": "none",
            "sizing": "equal", "equity": "1000", "symbols": ["BTC/USDT", "ETH/USDT"],
            "params": {"model": "gpt-6-astra", "effort": "high", "news": "false", "group": "5"}}
    r = _post(client, "/api/instances", body)
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["params"] == {"model": "gpt-6-astra", "effort": "high", "news": False, "group": 5}


def test_todays_universe_is_chosen_in_the_background(client, monkeypatch):
    from kripto_bot.core import universe
    done = []
    monkeypatch.setattr(universe, "select", lambda n, log=None: {"as_of": "2026-11-10T08:00:00+00:00", "n": n,
                                                                  "symbols": ["BTC/USDT"], "ranking": [], "skipped": []})
    monkeypatch.setattr(universe, "save", lambda chosen: done.append(chosen))
    r = _post(client, "/api/universes", {"n": 20})
    assert r.status_code == 202
    for _ in range(50):
        if not client.get("/api/universes").get_json()["running"]:
            break
        import time
        time.sleep(0.02)
    status = client.get("/api/universes").get_json()
    assert not status["running"] and status["error"] is None and done[0]["n"] == 20


def test_the_ai_analyst_tab_shows_plans_calls_events_and_orders(client, tmp_path, monkeypatch):
    import pandas as pd
    from kripto_bot.app import reporting
    from kripto_bot.llm import plan as pl
    from kripto_bot.llm import provider as p
    from kripto_bot.llm.store import LLMStore
    from kripto_bot.trading import llm_analyst, order_sim, store
    db = str(tmp_path / "paper.db")
    monkeypatch.setattr(store, "DB_FILE", db)
    inst = instances.create({"name": "v11-a", "strategy": "v11", "timeframe": "1d", "symbols": ["ETH/USDT"],
                             "params": {"model": "gpt-6-astra", "effort": "high"}})
    st = LLMStore(db)
    st.conn.executescript(llm_analyst.SCHEMA)
    now = pd.Timestamp.now(tz="UTC")
    call = st.record_call("v11-a", now, "scheduled", "group", ["ETH/USDT"], "prompt",
                          p.LLMResult(p.OK, usage={"input_tokens": 12000, "output_tokens": 300, "reasoning_output_tokens": 90},
                                      model="gpt-6-astra", effort="high"))
    plan = {"symbol": "ETH/USDT", "action": "enter", "entry": {"type": "limit", "price": 2950, "expires_candles": 1},
            "stop": 2850, "take_profit": 3150, "p_tp": 0.4, "grade": "strong", "review_if": [{"side": "below", "price": 2880}],
            "reasons": ["dip buy"], "news_ids": [], "memo": "watch ETF flows"}
    ctx = {"last": 3000, "atr": 120, "tick": 0.01}
    st.record_plan("v11-a", call, now, "group", "ETH/USDT", plan, pl.Verdict(True, [], plan), ctx)
    st.record_plan("v11-a", call, now, "group", "ETH/USDT", dict(plan, memo="x" * 400),
                   pl.Verdict(False, ["memo must be text of at most 300 characters"]), ctx)
    book = order_sim.Book("ETH/USDT", 1000.0)
    order_sim.apply(book, plan, now)
    st.conn.execute("INSERT INTO llm_books VALUES ('v11-a', 'ETH/USDT', ?, ?)",
                    (json.dumps(order_sim.to_dict(book)), now.isoformat()))
    st.conn.execute("INSERT INTO llm_events (instance, symbol, time, kind, data) VALUES "
                    "('v11-a', 'ETH/USDT', ?, 'review_skipped', '{\"reason\": \"3 triggered reviews today\"}')",
                    (now.isoformat(),))
    st.conn.commit()
    get = lambda t: client.get(f"/api/instances/v11-a/{t}").get_json()
    current = get("llm_current")
    assert len(current) == 1 and current[0]["memo"] == "watch ETF flows" and current[0]["p_tp"] == 0.4
    plans = get("llm_plans")
    assert [p_["accepted"] for p_ in plans] == [0, 1] and "memo must be" in plans[0]["rejected_because"]
    assert get("llm_orders")[0]["entry_price"] == 2950 and get("llm_orders")[0]["review_if"] == "below 2880"
    assert get("llm_calls")[0]["input_tokens"] == 12000
    assert get("llm_events")[0]["detail"] == "3 triggered reviews today"
    today = get("llm_today")
    assert today["calls"] == 1 and today["failed"] == 0 and today["stopped"] is None
    assert client.get("/instances/v11-a").status_code == 200
