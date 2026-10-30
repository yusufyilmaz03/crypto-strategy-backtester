# kripto_bot/app/telegram_commands.py
"""Telegram commands, answered by the supervisor (run_all.py) next to the notifications.

A background thread long-polls getUpdates, so nothing has to be reachable from the
internet. Only the chat in TELEGRAM_CHAT_ID is answered; anything else is ignored.

    /status     every instance: process state, return vs buy-and-hold, open positions
                (⭐ marks the panel's favorites, here and in the instance list)
    /instance   pick an instance, then its positions, signals and trades (buttons)
    /help       this list

Instances with manual close get a Close button per position. It asks for confirmation,
then files the same request as the panel's Close button (app/manual_close.py): the
instance's process sells and the notifier reports the trade. A confirmation pressed
more than CONFIRM_TTL_S after it was asked is refused, and close buttons stop working
when the supervisor restarts, so an old message can't close anything.

Logging: everything about closing a position (asked, requested, refused, too late,
expired) and messages from other chats always go to the supervisor's output, which
the background service writes to runtime/logs/supervisor.log. Read-only commands are
printed only when the supervisor runs in a terminal, so they don't fill the log.

Only one program may read a bot's updates at a time: while the supervisor runs,
`telegram-setup` gets a conflict error (and vice versa).
"""
import html
import json
import sys
import threading
import time

import pandas as pd

from kripto_bot import config, instances
from kripto_bot.app import manual_close, notify, reporting

POLL_TIMEOUT_S = 50     # long polling: Telegram holds the request until something arrives
MAX_AGE_S = 120         # commands older than this (sent while the supervisor was down) are ignored
CONFIRM_TTL_S = 120
ROWS = 10               # signals and trades per view
MAX_POSITIONS = 40
COMMANDS = [("status", "All instances at a glance"),
            ("instance", "Positions, signals and trades of one instance"),
            ("help", "What this bot can do")]
ACTIONS = ("buy/sell/trail: at the next open · holding: in a position · waiting: after an early exit · "
           "blocked: by a filter · ignored: no position to sell")
HELP = ("<b>Crypto Tester Bot</b>\n"
        "/status: all instances at a glance\n"
        "/instance: pick an instance for its positions, signals and trades\n"
        "/instance NAME: open one directly\n"
        "Instances with manual close get a Close button per position.")


def _button(text, data):
    return {"text": text, "callback_data": data}


def _name(inst):
    return f"{inst.name} ⭐" if inst.favorite else inst.name


def _date(t):
    return pd.Timestamp(t).strftime("%Y-%m-%d %H:%M") if t is not None and not pd.isna(t) else "?"


def _e(text):
    """Text for a message sent with parse_mode HTML."""
    return html.escape(str(text), quote=False)


def _missing(x):
    return x is None or pd.isna(x)


def _p1(x):
    return "–" if _missing(x) else "0.0" if abs(x) < 0.05 else f"{x:+.1f}"


def _price(x):
    return "–" if _missing(x) else f"{x:.5g}"


def _p2(x):
    return "–" if _missing(x) else f"{x:+.2f}"


def _f1(x):
    return "–" if _missing(x) else f"{x:.1f}"


def _f2(x):
    return "–" if _missing(x) else f"{x:.2f}"


def _settings(inst):
    """The instance's setup in one line, as on the panel's instance page (mode left out)."""
    params = " ".join(f"{k}={v}" for k, v in inst.params.items())
    parts = [f"{inst.strategy} {params}".strip(), inst.timeframe, f"stop {inst.stop}",
             inst.trail and f"trail {inst.trail}×ATR", inst.trend_sma and f"trend SMA{inst.trend_sma}",
             inst.lower_tf_entry and "lower-TF entry", inst.lower_tf_exit and "lower-TF exit",
             inst.lower_tf_sma and f"lower-TF SMA{inst.lower_tf_sma}", inst.manual_close and "manual close",
             inst.sizing != "equal" and f"{inst.sizing} sizing", f"{len(inst.symbol_list)} symbols"]
    return " · ".join(p for p in parts if p)


def _when(t, inst):
    """Candle or trade time, short: the date on 1d, else day and time."""
    if _missing(t):
        return "?"
    return pd.Timestamp(t).strftime("%m-%d" if inst.timeframe == "1d" else "%d %H:%M")


def _action(text):
    """What the engine did with a signal (run_realtime.describe_action), in a word."""
    text = text or ""
    for start, word in [("ignored (long-only)", "ignored"), ("ignored (already", "holding"),
                        ("ignored (waiting", "waiting"), ("ignored", "blocked"), ("enter", "buy"),
                        ("exit", "sell"), ("trailing", "trail"), ("lower-timeframe exit", "LTF exit"),
                        ("manual", "manual")]:
        if text.startswith(start):
            return word
    return text[:10]


def _money(x):
    return "–" if _missing(x) else f"{x:+.0f}" if abs(x) >= 100 else f"{x:+.2f}"


def _base(symbol):
    return symbol.removesuffix("/USDT")


def _short(text, n):
    return text if len(text) <= n else text[:n - 1] + "…"


def _table(headers, rows, align=None):
    """Rows of strings as a monospace table. align: "l" or "r" per column (default: the
    first left, the others right). Phones show about 34 characters per line before
    wrapping, so keep it narrow."""
    align = align or "l" + "r" * (len(headers) - 1)
    cells = [headers] + rows
    widths = [max(len(r[k]) for r in cells) for k in range(len(headers))]
    lines = [" ".join(c.ljust(w) if a == "l" else c.rjust(w) for c, w, a in zip(r, widths, align)).rstrip()
             for r in cells]
    lines[0] += "\u00a0" * 3      # room for the copy button Telegram puts over the block's top right
    return f"<pre>{_e(chr(10).join(lines))}</pre>"


class CommandBot:
    def __init__(self, token, chat_id, call=notify.telegram_call, instances_path=None, paper_db=None,
                 prices=None, clock=time.time, log=print, verbose=None):
        self.token, self.chat_id = token, str(chat_id)
        self.call, self.instances_path, self.paper_db = call, instances_path, paper_db
        self.prices = prices or reporting.current_prices
        self.clock, self.log = clock, log
        self.verbose = sys.stdout.isatty() if verbose is None else verbose   # read-only commands too
        self.offset = None
        self.targets = {}           # close button id -> (instance name, symbol); in memory on purpose
        self._ids = {}              # (instance name, symbol) -> close button id
        self._last_error = None
        self._stop = threading.Event()
        self._thread = None

    @property
    def enabled(self):
        return bool(self.token and self.chat_id)

    # ----- thread
    def start(self):
        if self.enabled and self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="telegram-commands")
            self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        try:
            self.call(self.token, "setMyCommands",
                      {"commands": json.dumps([{"command": c, "description": d} for c, d in COMMANDS])})
        except notify.TelegramError as e:
            self.log(f"[commands] setMyCommands failed: {e}")
        while not self._stop.is_set():
            try:
                self.poll_once()
                self._last_error = None
            except Exception as e:      # never stops the supervisor; say it once, then retry quietly
                text = str(e)
                if "Conflict" in text:
                    text += " (another program reads this bot's updates: telegram-setup or a second supervisor?)"
                if text != self._last_error:
                    self.log(f"[commands] {text}")
                self._last_error = text
                self._stop.wait(30)

    def poll_once(self, timeout=POLL_TIMEOUT_S):
        params = {"timeout": timeout, "allowed_updates": json.dumps(["message", "callback_query"])}
        if self.offset is not None:
            params["offset"] = self.offset
        for update in self.call(self.token, "getUpdates", params, timeout=timeout + 15):
            self.offset = update["update_id"] + 1
            try:
                self.handle(update)
            except Exception as e:
                self.log(f"[commands] {type(e).__name__}: {e}")

    # ----- logging
    def audit(self, text):
        """Always logged: closes and strangers."""
        self.log(f"{pd.Timestamp(self.clock(), unit='s', tz='UTC'):%Y-%m-%d %H:%M:%S} [telegram] {text}")

    def trace(self, text):
        """Logged in a terminal only: read-only commands."""
        if self.verbose:
            self.audit(text)

    # ----- updates
    def _ours(self, message, sender=None, what="message"):
        chat = ((message or {}).get("chat") or {}).get("id")
        if str(chat) == self.chat_id:
            return True
        who = (sender or (message or {}).get("from") or {})
        self.audit(f"ignored a {what} from chat {chat} (user {who.get('id')} @{who.get('username')}): "
                   "not TELEGRAM_CHAT_ID")
        return False

    def handle(self, update):
        if "callback_query" in update:
            return self._pressed(update["callback_query"])
        msg = update.get("message")
        if not self._ours(msg):
            return
        if self.clock() - msg.get("date", 0) > MAX_AGE_S:
            return self.trace(f"ignored {msg.get('text')!r}: sent while the supervisor was down")
        self.trace(msg.get("text"))
        word, *args = (msg.get("text") or "").split() or [""]
        command = word[1:].split("@")[0].lower() if word.startswith("/") else ""
        if command == "status":
            self._send(self.status_text())
        elif command == "instance" and args:
            self._send(*self.view("i", args[0]))
        elif command == "instance":
            self._send(*self.view("l", ""))
        else:
            self._send(HELP)

    def _pressed(self, query):
        msg = query.get("message")
        if msg is None:             # a press on a message too old for Telegram to send along
            return self.trace("button press without its message ignored")
        if not self._ours(msg, query.get("from"), "button press"):
            return
        kind, _, arg = (query.get("data") or "").partition("|")
        if kind not in ("c", "y"):
            self.trace(f"button {query.get('data')}")
        text, keyboard = self.view(kind, arg, msg)
        try:        # only stops the button's spinner; Telegram refuses it for a press answered late
            self.call(self.token, "answerCallbackQuery", {"callback_query_id": query["id"]})
        except notify.TelegramError:
            pass
        try:
            self.call(self.token, "editMessageText",
                      {"chat_id": self.chat_id, "message_id": msg["message_id"], "text": text, "parse_mode": "HTML",
                       "reply_markup": json.dumps({"inline_keyboard": keyboard})})
        except notify.TelegramError as e:
            if "not modified" not in str(e):     # Refresh with nothing new
                raise

    def _send(self, text, keyboard=None):
        params = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if keyboard:
            params["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
        self.call(self.token, "sendMessage", params)

    # ----- views: (HTML text, inline keyboard)
    def view(self, kind, arg, msg=None):
        if kind == "l":
            return self._list()
        if kind in ("c", "y"):
            target = self.targets.get(arg)
            if target is None:
                self.audit("close button from before a restart pressed: refused (expired)")
                return "This button has expired (the supervisor restarted). Open the instance again.", self._back()
            return self._confirm(arg, *target) if kind == "c" else self._close(arg, msg, *target)
        try:
            inst = instances.get(arg, self.instances_path)
        except instances.InstanceError:
            return f"There is no instance named {_e(arg)}.", self._back()
        views = {"i": self._instance, "p": self._positions, "s": self._signals, "t": self._trades}
        return views[kind](inst) if kind in views else self._list()

    def _back(self, inst=None):
        row = [_button("« Instances", "l")]
        if inst is not None:
            row.insert(0, _button("« " + inst.name, f"i|{inst.name}"))
        return [row]

    def _list(self):
        items = instances.load_all(self.instances_path).values()
        if not items:
            return "No instances yet. Create one on the panel.", []
        rows = [[_button(f"{'🟢' if i.enabled else '⚪️'} {_name(i)}", f"i|{i.name}")] for i in items]
        return "Pick an instance:", rows

    def _instance(self, inst):
        s = reporting.summary(inst, self.paper_db)
        state = reporting.service_status(inst.name)
        status = state["state"] + (f": {state['note']}" if state.get("note") else "")
        about = [f"<i>{_e(inst.description)}</i>", ""] if inst.description else []
        lines = [f"<b>{_e(_name(inst))}</b> - {inst.mode} - {_e(status)}",      # Telegram folds a long quote
                 "<blockquote expandable>" + "\n".join(about + [_e(_settings(inst))]) + "</blockquote>"]
        if s["return_pct"] is None:
            lines.append("No data yet.")
        else:
            lines.append(_table(["", "Strategy", "B&H"], [
                ["Return %", _p2(s["return_pct"]), _p2(s["buy_hold_pct"])],
                ["Sharpe", _f2(s["sharpe"]), _f2(s["buy_hold_sharpe"])],
                ["Max DD %", _f1(s["max_dd_pct"]), _f1(s["buy_hold_max_dd_pct"])]]))
            win = "–" if _missing(s["win_rate_pct"]) else f"{s['win_rate_pct']:.0f}%"
            lines.append(_table(["Days", "Open", "Closed", "Win", "PnL"],
                                [[str(s["days"]), str(s["open_positions"]), str(s["closed_trades"]), win,
                                  _money(s["realized_pnl"])]], "rrrrr"))
            lines.append("PnL: realized, closed trades, USDT.")
        keyboard = [[_button(f"Positions ({s['open_positions']})", f"p|{inst.name}"),
                     _button("Signals", f"s|{inst.name}"), _button("Trades", f"t|{inst.name}")],
                    [_button("↻ Refresh", f"i|{inst.name}"), _button("« Instances", "l")]]
        return "\n".join(lines), keyboard

    def _positions(self, inst):
        pos = reporting.positions(inst, self.paper_db)
        if pos.empty:
            return f"<b>{_e(inst.name)}</b>: no open positions.", self._back(inst)
        prices = self.prices(set(pos["symbol"]))
        rows, stops, closable, requested = [], [], [], False
        for _, p in pos.head(MAX_POSITIONS).iterrows():
            now = prices.get(p["symbol"])
            change = (now / p["entry_price"] - 1) * 100 if now and p["entry_price"] else None
            base = _base(p["symbol"])
            if p.get("close_requested"):
                requested = True
                base += "*"
            elif inst.mode == "paper" and inst.manual_close:
                closable.append(p["symbol"])
            rows.append([base, _price(p["entry_price"]), _price(now), _p1(change)])
            if p.get("stop") is not None and not pd.isna(p.get("stop")):
                stops.append(f"{_base(p['symbol'])} {_price(p['stop'])}")
        parts = [f"<b>{_e(inst.name)}</b> · {len(pos)} open", _table(["Symbol", "Entry", "Now", "Δ%"], rows)]
        if len(pos) > MAX_POSITIONS:
            parts.append(f"… and {len(pos) - MAX_POSITIONS} more on the panel")
        if stops:
            parts.append("Stops: " + _e(", ".join(stops)))
        if requested:
            parts.append("* close requested")
        parts.append("Δ%: last price vs entry, before fees.")
        buttons = [_button(f"Close {_base(s)}", f"c|{self._target(inst.name, s)}") for s in closable]
        keyboard = [buttons[k:k + 3] for k in range(0, len(buttons), 3)]
        keyboard.append([_button("↻ Refresh", f"p|{inst.name}")] + self._back(inst)[0])
        return "\n".join(parts), keyboard

    def _target(self, name, symbol):
        key = (name, symbol)
        if key not in self._ids:
            self._ids[key] = str(len(self._ids) + 1)
            self.targets[self._ids[key]] = key
        return self._ids[key]

    def _signals(self, inst):
        df = reporting.signals(inst, ROWS, self.paper_db)
        if df.empty:
            return f"<b>{_e(inst.name)}</b>: no signals yet.", self._back(inst)
        title = f"<b>{_e(inst.name)}</b> · last {'signals' if inst.mode == 'paper' else 'events'} (newest first)"
        if inst.mode == "paper":
            rows = [[_when(r.candle_time, inst), _base(r.symbol), r.signal, _action(r.action)] for r in df.itertuples()]
            return "\n".join([title, _table(["Candle", "Symbol", "Sig", "Action"], rows, "llll"), ACTIONS]), \
                self._back(inst)
        rows = [f"{_date(r.time)} {r.level}{' ' + r.symbol if r.symbol else ''}: {r.message}" for r in df.itertuples()]
        return "\n".join([title] + [_e(r) for r in rows]), self._back(inst)

    def _trades(self, inst):
        df = reporting.trades(inst, self.paper_db)
        if inst.mode == "paper" and not df.empty:
            df = df[df["exit_time"].notna()].sort_values("exit_time")
        if df.empty:
            return f"<b>{_e(inst.name)}</b>: no closed trades yet.", self._back(inst)
        df = df.tail(ROWS).iloc[::-1]
        if inst.mode == "paper":
            rows = [[_when(r.exit_time, inst), _base(r.symbol), _p1(r.return_pct), _money(r.pnl),
                     r.reason or "SIGNAL"] for r in df.itertuples()]
            body = _table(["Closed", "Symbol", "Ret%", "PnL", "Exit"], rows, "llrrl")
            return f"<b>{_e(inst.name)}</b> · last closed trades (newest first)\n{body}", self._back(inst)
        rows = [[_when(r.created_at, inst), str(r.side).upper(), _base(r.symbol), _price(r.average),
                 str(r.status or "")[:6]] for r in df.itertuples()]
        body = _table(["Time", "Side", "Symbol", "Price", "Status"], rows, "lllrl")
        return f"<b>{_e(inst.name)}</b> · last orders (newest first)\n{body}", self._back(inst)

    def _confirm(self, target_id, name, symbol):
        self.audit(f"close {symbol} on {name}: asked for confirmation")
        text = (f"<b>Close {_e(symbol)} on {_e(name)} at the market now?</b>\n"
                "The instance sells at the last price within about a minute; "
                "the trade is recorded with reason MANUAL.")
        return text, [[_button("Yes, close", f"y|{target_id}"), _button("Cancel", f"p|{name}")]]

    def _close(self, target_id, msg, name, symbol):
        back = [[_button("Positions", f"p|{name}"), _button("« Instances", "l")]]
        asked = (msg or {}).get("edit_date") or (msg or {}).get("date", 0)
        if self.clock() - asked > CONFIRM_TTL_S:
            self.audit(f"close {symbol} on {name}: confirmed too late, not closed")
            return f"Too late: {_e(symbol)} was not closed. Press Close again.", back
        try:
            manual_close.request(instances.get(name, self.instances_path), symbol, self.paper_db)
        except (manual_close.CloseRefused, instances.InstanceError) as e:
            self.audit(f"close {symbol} on {name}: refused ({e})")
            return f"{_e(symbol)} not closed: {_e(e)}", back
        self.audit(f"close {symbol} on {name}: confirmed, request filed")
        return (f"✋ Close of {_e(symbol)} requested. {_e(name)} sells it within about a minute; "
                "the trade message follows."), back

    # ----- /status
    def status_text(self):
        rows, down, stopped = [], [], []
        items = instances.load_all(self.instances_path).values()
        for inst in items:
            if not inst.enabled:
                stopped.append(_name(inst))
                continue
            state = reporting.service_status(inst.name)["state"]
            if state != "running":
                down.append(f"{inst.name} ({state})")
            s = reporting.summary(inst, self.paper_db)
            rows.append([("*" if inst.favorite else " ") + _short(inst.name, 15), _p1(s["return_pct"]),
                         _p1(s["buy_hold_pct"]), str(s["open_positions"])])
        parts = [f"<b>📊 Status {pd.Timestamp(self.clock(), unit='s', tz='UTC'):%Y-%m-%d %H:%M} UTC</b>"]
        parts.append(_table([" Instance", "Ret%", "B&H%", "Open"], rows) if rows else "No running instances.")
        if any(i.favorite for i in items if i.enabled):
            parts.append("* favorite")
        if down:
            parts.append("Not running: " + _e(", ".join(down)))
        if stopped:
            parts.append("Stopped: " + _e(", ".join(stopped)))
        parts.append(f"Kill switch: {notify.kill_state()}")
        return "\n".join(parts)


def from_config(log=print):
    return CommandBot(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, log=log)
