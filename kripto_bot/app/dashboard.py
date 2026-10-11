# kripto_bot/app/dashboard.py
"""Flask control panel: overview, bot instances, playground, settings and backtests.

Binds to 127.0.0.1 by default. Requests that change something must carry the header
X-Requested-With: kripto-bot; browsers don't let other websites send it, which keeps
pages you visit from driving the panel behind your back (CSRF).

On a server (Docker) set DASHBOARD_HOST=0.0.0.0 and DASHBOARD_PASSWORD in .env: every
request then needs HTTP basic auth (any user name). The panel refuses to listen
beyond 127.0.0.1 without a password.
"""
import hmac
import importlib
import json
import os

import pandas as pd
from flask import Flask, Response, jsonify, render_template, request

from kripto_bot import config
from kripto_bot import instances, paths
from kripto_bot.app import notify, playground
from kripto_bot.app import reporting
from kripto_bot import settings
from kripto_bot.core import strategy

app = Flask(__name__)

BACKTEST_FILE = paths.results("multi_backtest_strategies.csv")
CSRF_HEADER = ("X-Requested-With", "kripto-bot")


class ApiError(Exception):
    pass


def _password():
    return os.environ.get("DASHBOARD_PASSWORD", "")


@app.before_request
def _auth_guard():
    pw = _password()
    if not pw:
        return None
    auth = request.authorization
    if auth and auth.password and hmac.compare_digest(auth.password.encode(), pw.encode()):
        return None
    return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="kripto-bot"'})


@app.before_request
def _csrf_guard():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.headers.get(CSRF_HEADER[0]) != CSRF_HEADER[1]:
            return jsonify({"error": "missing request header"}), 403
    return None


@app.errorhandler(instances.InstanceError)
@app.errorhandler(settings.SettingsError)
@app.errorhandler(playground.PlaygroundError)
@app.errorhandler(ApiError)
def _bad_request(e):
    return jsonify({"error": str(e)}), 400


def records(df):
    """DataFrame -> JSON-safe list of dicts (NaN -> null)."""
    if df is None or df.empty:
        return []
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _inst(name):
    return instances.get(name)


def _instance_view(inst):
    d = {k: getattr(inst, k) for k in ("name", "mode", "strategy", "params", "timeframe", "stop",
                                       "equity", "enabled", "description", "created_at", "run_key")}
    d["symbols"] = inst.symbol_list
    d["custom_symbols"] = bool(inst.symbols)
    d["status"] = reporting.service_status(inst.name)
    d["summary"] = reporting.summary(inst)
    return d


def _kill_state():
    path = config.KILL_SWITCH_FILE
    if not os.path.exists(path):
        return "off"
    with open(path) as f:
        return "flatten" if "flatten" in f.read().lower() else "block"


# ---------------------------------------------------------------- pages
@app.route("/")
def overview():
    return render_template("overview.html", page="overview")


@app.route("/instances")
def instances_page():
    return render_template("instances.html", page="instances")


@app.route("/instances/<name>")
def instance_page(name):
    _inst(name)
    return render_template("instance.html", page="instances", name=name)


@app.route("/playground")
def playground_page():
    return render_template("playground.html", page="playground")


@app.route("/settings")
def settings_page():
    return render_template("settings.html", page="settings")


@app.route("/backtest")
def backtest():
    return render_template("backtest.html", page="backtest")


@app.route("/health")
def health():
    return jsonify({"ok": True})


# ---------------------------------------------------------------- overview
@app.route("/api/overview")
def api_overview():
    status = {"running": False}
    if os.path.exists(reporting.STATUS_FILE):
        try:
            with open(reporting.STATUS_FILE) as f:
                st = json.load(f)
            age = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(st["updated_at"])).total_seconds()
            status = {"running": age < 15, "updated_at": st["updated_at"], "pid": st.get("supervisor_pid")}
        except (OSError, ValueError, KeyError):
            pass
    return jsonify({
        "supervisor": status,
        "kill_switch": _kill_state(),
        "trading_mode": config.TRADING_MODE, "live_enabled": config.LIVE_TRADING_ENABLED,
        "instances": [_instance_view(i) for i in instances.load_all().values()],
    })


@app.route("/api/kill-switch", methods=["GET", "POST"])
def api_kill_switch():
    if request.method == "POST":
        mode = (request.get_json(silent=True) or {}).get("mode")
        if mode == "off":
            if os.path.exists(config.KILL_SWITCH_FILE):
                os.remove(config.KILL_SWITCH_FILE)
        elif mode in ("block", "flatten"):
            with open(config.KILL_SWITCH_FILE, "w") as f:
                f.write("flatten" if mode == "flatten" else "")
        else:
            raise ApiError("mode must be off, block or flatten")
    return jsonify({"kill_switch": _kill_state()})


# ---------------------------------------------------------------- instances
@app.route("/api/strategies")
def api_strategies():
    return jsonify({
        "strategies": [{"key": k, "description": strategy.STRATEGY_INFO.get(k, ""),
                        "params": instances.param_spec(k), "grid": {
                            (" / ".join(n) if isinstance(n, tuple) else n): v
                            for n, v in strategy.PARAM_GRIDS.get(k, {}).items()}}
                       for k in strategy.STRATEGY_DISPATCH],
        "timeframes": instances.TIMEFRAMES, "modes": instances.MODES,
        "stops": dict(config.STOPLOSS_CONFIG),
        "symbols": list(config.SYMBOLS), "default_equity": config.INITIAL_EQUITY,
        "default_capital": config.TRADING_CAPITAL_USDT,
    })


@app.route("/api/instances", methods=["GET", "POST"])
def api_instances():
    if request.method == "POST":
        inst = instances.create(request.get_json(silent=True) or {})
        return jsonify(_instance_view(inst)), 201
    return jsonify([_instance_view(i) for i in instances.load_all().values()])


@app.route("/api/instances/<name>", methods=["GET", "DELETE"])
def api_instance(name):
    if request.method == "DELETE":
        instances.delete(name)
        return jsonify({"deleted": name})
    return jsonify(_instance_view(_inst(name)))


@app.route("/api/instances/<name>/action", methods=["POST"])
def api_instance_action(name):
    d = request.get_json(silent=True) or {}
    action = d.get("action")
    if action == "start":
        inst = instances.update(name, enabled=True)
    elif action == "stop":
        inst = instances.update(name, enabled=False)
    elif action == "restart":
        inst = instances.update(name, restart_requested_at=pd.Timestamp.now(tz="UTC").isoformat())
    elif action == "describe":
        inst = instances.update(name, description=str(d.get("description", ""))[:500])
    else:
        raise ApiError("action must be start, stop, restart or describe")
    return jsonify(_instance_view(inst))


@app.route("/api/instances/<name>/curves")
def api_instance_curves(name):
    strat, bh = reporting.curves(_inst(name))
    return jsonify({"labels": [t.isoformat() for t in strat.index],
                    "strategy": [round(float(v), 4) for v in strat],
                    "buy_hold": [None if pd.isna(v) else round(float(v), 4) for v in bh]})


@app.route("/api/instances/<name>/<table>")
def api_instance_table(name, table):
    inst = _inst(name)
    if table == "positions":
        return jsonify(records(reporting.positions(inst)))
    if table == "trades":
        return jsonify(records(reporting.trades(inst).iloc[::-1]))
    if table == "signals":
        return jsonify(records(reporting.signals(inst)))
    if table == "log":
        return jsonify(reporting.log_tail(inst.name, int(request.args.get("lines", 200))))
    raise ApiError(f"unknown table {table}")


# ---------------------------------------------------------------- playground
@app.route("/api/playground/run", methods=["POST"])
def api_playground_run():
    d = request.get_json(silent=True) or {}
    try:
        result = playground.run(d.get("symbol", ""), d.get("timeframe", "1d"), d.get("strategy", "v9"),
                                d.get("params") or {}, d.get("stop", "none"), d.get("days", 365),
                                bool(d.get("include_holdout")))
    except playground.PlaygroundError:
        raise
    except Exception as e:  # e.g. a symbol Binance doesn't list
        raise ApiError(f"{type(e).__name__}: {e}")
    return jsonify(result)


@app.route("/api/playground/history")
def api_playground_history():
    total, rows = playground.history()
    return jsonify({"total": total, "runs": records(rows)})


# ---------------------------------------------------------------- settings
def _settings_view():
    overrides = settings.load_overrides()
    groups = {}
    for s in settings.SCHEMA:
        groups.setdefault(s.group, []).append({
            "key": s.key, "label": s.label, "kind": s.kind, "help": s.help, "min": s.min, "max": s.max,
            "choices": s.choices, "editable": s.editable, "restart": s.restart,
            "value": getattr(config, s.key), "overridden": s.key in overrides,
        })
    return {"groups": groups, "secrets": {
        "Binance API key": bool(config.BINANCE_API_KEY and config.BINANCE_API_SECRET),
        "Testnet API key": bool(config.BINANCE_TESTNET_API_KEY and config.BINANCE_TESTNET_API_SECRET),
        "Telegram bot": bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)}}


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        for key in d.get("reset", []):
            settings.reset(key)
        _, restart = settings.save(d.get("changes", {}))
        importlib.reload(config)
        view = _settings_view()
        view["restart"] = restart
        return jsonify(view)
    return jsonify(_settings_view())


@app.route("/api/settings/restart", methods=["POST"])
def api_settings_restart():
    """Restart the running instances affected by a settings change."""
    services = set((request.get_json(silent=True) or {}).get("services", []))
    now = pd.Timestamp.now(tz="UTC").isoformat()
    done = []
    for inst in instances.load_all().values():
        if inst.enabled and inst.mode in services:
            instances.update(inst.name, restart_requested_at=now)
            done.append(inst.name)
    return jsonify({"restarted": done})


@app.route("/api/notify/test", methods=["POST"])
def api_notify_test():
    sender = notify.from_config()
    if not sender.enabled:
        raise ApiError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are not set in .env")
    try:
        sender.send_now("✅ Test message from the kripto-bot panel.")
    except notify.TelegramError as e:
        raise ApiError(f"Telegram: {e}")
    return jsonify({"sent": True})


# ---------------------------------------------------------------- backtests
@app.route("/api/backtest")
def api_backtest():
    """multi_backtest_strategies.csv as JSON (one row per symbol/timeframe/strategy/segment)."""
    if not os.path.exists(BACKTEST_FILE):
        return jsonify({"results": []})
    df = pd.read_csv(BACKTEST_FILE)
    if df.empty or "Segment" not in df.columns:
        return jsonify({"results": []})
    df = df.replace([float("inf"), float("-inf")], None)
    return jsonify({"results": json.loads(df.to_json(orient="records"))})


def bind_host():
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost") and not _password():
        raise SystemExit(f"Refusing to listen on {host} without DASHBOARD_PASSWORD (set it in .env).")
    return host


if __name__ == "__main__":
    host = bind_host()
    print(f"Starting dashboard on http://{host}:5002 ...")
    # Debug mode reloads on code changes; run_all.py turns it off (DASHBOARD_DEBUG=0)
    # because the reloader runs the app in a child process the supervisor can't see.
    # Never on a network: the debugger would let anyone run code.
    debug = os.environ.get("DASHBOARD_DEBUG", "1") != "0" and host in ("127.0.0.1", "localhost")
    app.run(host=host, port=5002, debug=debug, use_reloader=debug)
