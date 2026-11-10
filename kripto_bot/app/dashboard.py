# kripto_bot/app/dashboard.py
"""Flask control panel: overview, bot instances, playground, settings and backtests.

Binds to 127.0.0.1 by default. Requests that change something must carry the header
X-Requested-With: kripto-bot; browsers don't let other websites send it, which keeps
pages you visit from driving the panel behind your back (CSRF).

On a server (Docker) set DASHBOARD_HOST=0.0.0.0 and DASHBOARD_PASSWORD in .env: requests
then need HTTP basic auth (any user name). The panel refuses to listen beyond 127.0.0.1
without a password. Opened directly on this computer (http://127.0.0.1:5002) it doesn't
ask: whatever runs here could read .env anyway. Requests relayed by a proxy such as
`tailscale serve` also come from 127.0.0.1, but they carry X-Forwarded-* headers and
the proxy's host name, so they still need the password.
"""
import hmac
import importlib
import json
import logging
import math
import os
import threading
import urllib.parse

import pandas as pd
from flask import Flask, Response, jsonify, render_template, request
from markupsafe import Markup

from kripto_bot import config
from kripto_bot import instances, paths
from kripto_bot.app import i18n, manual_close, notify, playground
from kripto_bot.app import reporting
from kripto_bot import settings
from kripto_bot.core import bankroll, strategy, universe
from kripto_bot.core.lower_timeframes import LOWER_TIMEFRAMES
from kripto_bot.trading import store

app = Flask(__name__)

BACKTEST_FILE = paths.results("multi_backtest_strategies.csv")
DEFAULT_BANKROLL = 10_000.0     # New instance modal: starting bankroll of sizing "bankroll"
CSRF_HEADER = ("X-Requested-With", "kripto-bot")
LOOPBACK = ("127.0.0.1", "::1")
PROXY_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "Forwarded")


class ApiError(Exception):
    pass


def _password():
    return os.environ.get("DASHBOARD_PASSWORD", "")


def _direct_local():
    """Typed on this computer, not relayed by a proxy (see the module docstring)."""
    host = urllib.parse.urlsplit("//" + request.host).hostname
    return (request.remote_addr in LOOPBACK and host in LOOPBACK + ("localhost",)
            and not any(h in request.headers for h in PROXY_HEADERS))


@app.before_request
def _auth_guard():
    pw = _password()
    if not pw or _direct_local():
        return None
    auth = request.authorization
    if auth and auth.password and hmac.compare_digest(auth.password.encode(), pw.encode()):
        return None
    return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Crypto Tester Bot"'})


@app.before_request
def _csrf_guard():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.headers.get(CSRF_HEADER[0]) != CSRF_HEADER[1]:
            return jsonify({"error": "missing request header"}), 403
    return None


@app.errorhandler(instances.InstanceError)
@app.errorhandler(settings.SettingsError)
@app.errorhandler(playground.PlaygroundError)
@app.errorhandler(manual_close.CloseRefused)
@app.errorhandler(ApiError)
def _bad_request(e):
    return jsonify({"error": str(e)}), 400


@app.context_processor
def _language():
    """Templates get _() and the page's language; Turkish pages also get the catalog for app.js.
    code() marks a value of _() as <code> (other values are escaped)."""
    lang = i18n.lang_of(request)
    return {"lang": lang, "_": lambda text, **kw: i18n.translate(text, lang, **kw),
            "code": lambda text: Markup("<code>{}</code>").format(text), "i18n_catalog": i18n.catalog(lang)}


def records(df):
    """DataFrame -> JSON-safe list of dicts (NaN -> null)."""
    if df is None or df.empty:
        return []
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _inst(name):
    return instances.get(name)


def _instance_view(inst):
    d = {k: getattr(inst, k) for k in ("name", "mode", "strategy", "params", "timeframe", "stop", "trail", "sizing",
                                       "lower_tf_entry", "lower_tf_exit", "lower_tf_sma", "trend_sma", "manual_close",
                                       "equity", "enabled", "favorite", "exit_only", "stopped_reason",
                                       "stopped_at", "description",
                                       "created_at", "run_key", "stakes", "min_entry", "max_entry", "score_inputs",
                                       "rotation", "rotate_profit_only", "rotate_trim", "low_basis", "low_pct")}
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
                       for k in strategy.STRATEGY_DISPATCH]
                      + [{"key": instances.ANALYST, "description": instances.ANALYST_INFO,
                          "params": instances.ANALYST_PARAMS, "grid": {}, "analyst": True}],
        "universes": universe.saved(),
        "timeframes": instances.TIMEFRAMES, "modes": instances.MODES, "sizings": instances.SIZINGS,
        "stops": dict(config.STOPLOSS_CONFIG), "lower_timeframes": LOWER_TIMEFRAMES,
        "symbols": list(config.SYMBOLS), "default_equity": config.INITIAL_EQUITY,
        "default_capital": config.TRADING_CAPITAL_USDT,
        "bankroll": {"grades": bankroll.GRADES, "grade_from": bankroll.GRADE_FROM,
                     "default_stakes": bankroll.DEFAULT_STAKES, "default_min_entry": bankroll.DEFAULT_MIN_ENTRY,
                     "min_order": bankroll.MIN_ORDER, "inputs": bankroll.INPUTS,
                     "default_inputs": bankroll.DEFAULT_INPUTS, "low_bases": bankroll.LOW_BASES,
                     "default_low_pct": bankroll.DEFAULT_LOW_PCT, "default_bankroll": DEFAULT_BANKROLL,
                     "rotate": {"margin": bankroll.ROTATE_MARGIN, "min_hold": bankroll.ROTATE_MIN_HOLD,
                                "max": bankroll.ROTATE_MAX}},
    })


UNIVERSE_JOB = {"running": False, "error": None}


def _pick_universe(n):
    try:
        universe.save(universe.select(n, log=lambda *_: None))
        UNIVERSE_JOB["error"] = None
    except Exception as e:      # shown in the panel; the next click tries again
        UNIVERSE_JOB["error"] = f"{type(e).__name__}: {e}"
    finally:
        UNIVERSE_JOB["running"] = False


@app.route("/api/universes", methods=["GET", "POST"])
def api_universes():
    """Saved universes; POST chooses today's most traded pairs in the background (a few minutes)."""
    if request.method == "POST":
        if UNIVERSE_JOB["running"]:
            raise ApiError("a universe is already being chosen")
        UNIVERSE_JOB.update(running=True, error=None)
        threading.Thread(target=_pick_universe, args=(int((request.get_json(silent=True) or {}).get("n", 20)),),
                         daemon=True).start()
        return jsonify({"running": True}), 202
    return jsonify({**UNIVERSE_JOB, "universes": universe.saved()})


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
        inst = instances.update(name, enabled=True, stopped_reason=None, stopped_at=None)
    elif action == "stop":
        inst = instances.update(name, enabled=False)
    elif action == "restart":
        inst = instances.update(name, restart_requested_at=pd.Timestamp.now(tz="UTC").isoformat())
    elif action == "describe":
        inst = instances.update(name, description=str(d.get("description", ""))[:500])
    elif action == "favorite":
        inst = instances.update(name, favorite=bool(d.get("favorite")))
    elif action == "low":
        inst = instances.update(name, low_basis=d.get("low_basis"), low_pct=d.get("low_pct"))
    elif action == "exit_only":
        inst = instances.update(name, exit_only=bool(d.get("exit_only")))
    elif action == "flatten":
        # exit only first, so the instance doesn't buy back what it sells
        asked = manual_close.flatten(_inst(name))
        inst = instances.update(name, exit_only=True)
        return jsonify({**_instance_view(inst), "flatten": asked})
    else:
        raise ApiError("action must be start, stop, restart, describe, favorite, low, exit_only or flatten")
    return jsonify(_instance_view(inst))


@app.route("/api/instances/<name>/curves")
def api_instance_curves(name):
    strat, bh = reporting.curves(_inst(name))
    return jsonify({"labels": [t.isoformat() for t in strat.index],
                    "strategy": [round(float(v), 4) for v in strat],
                    "buy_hold": [None if pd.isna(v) else round(float(v), 4) for v in bh]})


@app.route("/api/instances/<name>/bankroll", methods=["POST"])
def api_instance_bankroll(name):
    """File a deposit or withdrawal; the instance's process applies it (books only, no
    money moves on an exchange)."""
    inst = _inst(name)
    d = request.get_json(silent=True) or {}
    kind = d.get("kind")
    if inst.sizing != "bankroll":
        raise ApiError(f"{inst.name} has no bankroll")
    if kind not in ("deposit", "withdraw"):
        raise ApiError("kind must be deposit or withdraw")
    try:
        amount = float(d.get("amount"))
    except (TypeError, ValueError):
        raise ApiError("amount must be a number")
    if not 0 < amount <= 10_000_000 or math.isnan(amount):
        raise ApiError("amount must be above 0 and at most 10,000,000 USDT")
    view = reporting.bankroll_view(inst)
    if kind == "withdraw" and view.get("started") and amount > view["free"] + 1e-9:
        raise ApiError(f"only {view['free']:.2f} USDT is free; money in positions can't be withdrawn")
    paper = store.PaperStore()
    try:
        paper.request_bankroll(inst.run_key, inst.timeframe, kind, amount, str(d.get("note") or "")[:200], "panel")
    finally:
        paper.close()
    when = "within a minute" if inst.enabled else "when the instance starts"
    return jsonify({"requested": kind, "amount": amount, "applied": when})


@app.route("/api/instances/<name>/close", methods=["POST"])
def api_instance_close(name):
    """Ask a paper instance's process to sell one position at the market."""
    symbol = (request.get_json(silent=True) or {}).get("symbol")
    manual_close.request(_inst(name), symbol)
    return jsonify({"requested": symbol})


@app.route("/api/instances/<name>/<table>")
def api_instance_table(name, table):
    inst = _inst(name)
    if table == "positions":
        return jsonify(records(reporting.positions(inst)))
    if table == "prices":
        # separate from the tables, which mustn't wait for Binance
        return jsonify(reporting.current_prices(set(reporting.positions(inst).get("symbol", []))))
    if table == "trades":
        return jsonify(records(reporting.trades(inst).iloc[::-1]))
    if table == "signals":
        return jsonify(records(reporting.signals(inst)))
    if table == "stops":
        return jsonify(records(reporting.stop_events(inst).iloc[::-1]))
    if table == "ledger":
        return jsonify(records(reporting.ledger(inst)))
    if table == "requests":
        return jsonify(records(reporting.bankroll_requests(inst)))
    if table == "snapshots":
        snaps = reporting.snapshots(inst)
        return jsonify(records(snaps.assign(time=snaps["time"].dt.strftime("%Y-%m-%dT%H:%M:%S")) if not snaps.empty else snaps))
    if table == "log":
        return jsonify(reporting.log_tail(inst.name, int(request.args.get("lines", 200))))
    analyst = {"llm_plans": reporting.llm_plans, "llm_current": reporting.llm_current, "llm_calls": reporting.llm_calls,
               "llm_events": reporting.llm_events, "llm_orders": reporting.llm_orders}
    if table in analyst:
        return jsonify(records(analyst[table](inst)))
    if table == "llm_today":
        return jsonify(reporting.llm_today(inst))
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
        sender.send_now("✅ Test message from the Crypto Tester Bot panel.")
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


class _SkipSuccessfulRequests(logging.Filter):
    """Drops werkzeug's access-log lines for 2xx/3xx responses: the open panel polls every
    few seconds, so they were nearly all of the log. Errors and 4xx/5xx lines stay."""

    def filter(self, record):
        args = record.args if isinstance(record.args, tuple) else ()
        return not (len(args) == 3 and str(args[1])[:1] in ("2", "3"))


def quiet_access_log():
    logging.getLogger("werkzeug").addFilter(_SkipSuccessfulRequests())


def bind_host():
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost") and not _password():
        raise SystemExit(f"Refusing to listen on {host} without DASHBOARD_PASSWORD (set it in .env).")
    return host


if __name__ == "__main__":
    host = bind_host()
    quiet_access_log()
    print(f"Starting dashboard on http://{host}:5002 ...")
    # Debug mode reloads on code changes; run_all.py turns it off (DASHBOARD_DEBUG=0)
    # because the reloader runs the app in a child process the supervisor can't see.
    # Never on a network: the debugger would let anyone run code.
    debug = os.environ.get("DASHBOARD_DEBUG", "1") != "0" and host in ("127.0.0.1", "localhost")
    app.run(host=host, port=5002, debug=debug, use_reloader=debug)
