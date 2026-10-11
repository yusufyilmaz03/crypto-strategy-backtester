# kripto_bot/app/run_all.py
"""Run the dashboard and every enabled bot instance from one terminal.

The supervisor follows runtime/instances.json: an instance that is enabled (from
the dashboard or the file) gets its own process (kripto_bot.trading.run_realtime or
run_testnet --instance NAME), a disabled or deleted one is stopped, and a restart
requested from the dashboard restarts it. Output is shown with a [name] prefix and
written to runtime/logs/<name>.log. A process that exits is restarted after a pause
(doubling up to 5 minutes while it keeps failing). Current states go to
runtime/status.json for the dashboard. Ctrl+C stops everything.

    python -m kripto_bot run                  # dashboard + enabled instances
    python -m kripto_bot run --no-dashboard   # instances only
    python -m kripto_bot run --keep-awake     # macOS: no idle sleep while running
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time

import pandas as pd

from kripto_bot import config, instances, paths
from kripto_bot.app import notify
from kripto_bot.trading.store import AlreadyRunning, single_instance

LOG_DIR = paths.LOG_DIR
STATUS_FILE = paths.runtime("status.json")
PALETTE = ["\033[36m", "\033[35m", "\033[32m", "\033[34m", "\033[91m", "\033[96m"]
DASHBOARD_COLOR = "\033[33m"
RESET = "\033[0m"
MIN_BACKOFF_S, MAX_BACKOFF_S = 5, 300
STABLE_AFTER_S = 600       # a run this long resets the backoff
RELOAD_EVERY_S = 2.0


class Service:
    def __init__(self, name, cmd, log_dir=None, color="", on_exit=None):
        log_dir = log_dir or LOG_DIR
        self.name, self.cmd = name, cmd
        self.prefix = f"{color}[{name}]{RESET if color else ''}"
        os.makedirs(log_dir, exist_ok=True)
        self.log = open(os.path.join(log_dir, f"{name}.log"), "a", buffering=1)
        self.proc = None
        self.started = 0.0
        self.backoff = MIN_BACKOFF_S
        self.next_start = 0.0
        self.restarts = 0
        self.last_exit = None
        self.note = ""
        self.on_exit = on_exit      # called with (name, code, seconds ran, STABLE_AFTER_S)

    def start(self):
        env = dict(os.environ, PYTHONUNBUFFERED="1", DASHBOARD_DEBUG="0")
        self.proc = subprocess.Popen(self.cmd, cwd=paths.ROOT, env=env, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, bufsize=1,
                                     start_new_session=True)  # Ctrl+C goes to the supervisor only
        self.started = time.time()
        self.note = ""
        self.emit(f"started (pid {self.proc.pid})")
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc):
        for line in proc.stdout:
            self.emit(line.rstrip("\n"))

    def emit(self, line):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"{self.prefix} {line}", flush=True)
        self.log.write(f"{stamp} {line}\n")

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def check(self):
        """Start the service if it is due, or schedule a restart if it has exited."""
        now = time.time()
        if self.proc is None:
            if now >= self.next_start:
                self.start()
            return
        code = self.proc.poll()
        if code is None:
            return
        ran = now - self.started
        self.backoff = MIN_BACKOFF_S if ran >= STABLE_AFTER_S else min(self.backoff * 2, MAX_BACKOFF_S)
        self.emit(f"exited with code {code} after {ran:.0f}s; restarting in {self.backoff}s")
        self.last_exit = code
        self.proc = None
        self.restarts += 1
        self.next_start = now + self.backoff
        if self.on_exit:
            self.on_exit(self.name, code, ran, STABLE_AFTER_S)

    def stop(self, timeout=10):
        if self.running:
            self.proc.terminate()
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.emit("stopped")
        self.proc = None

    def status(self):
        state = "running" if self.running else ("waiting to restart" if self.restarts else "starting")
        return {"state": state, "pid": self.proc.pid if self.running else None,
                "started_at": pd.Timestamp(self.started, unit="s", tz="UTC").isoformat() if self.started else None,
                "restarts": self.restarts, "last_exit_code": self.last_exit, "note": self.note}


def instance_command(inst):
    module = "kripto_bot.trading.run_realtime" if inst.mode == "paper" else "kripto_bot.trading.run_testnet"
    return [sys.executable, "-u", "-m", module, "--instance", inst.name]


class Supervisor:
    def __init__(self, with_dashboard=True, instances_path=None, log_dir=None, status_file=None, watcher=None):
        log_dir = log_dir or LOG_DIR
        status_file = status_file or STATUS_FILE
        self.instances_path = instances_path
        self.log_dir = log_dir
        self.status_file = status_file
        self.services = {}          # service name -> Service
        self.blocked = {}           # instance name -> reason it can't start
        self.last_reload = 0.0
        self.watcher = watcher      # notify.Watcher or None
        on_exit = watcher.service_exited if watcher else None
        self.on_exit = on_exit
        if with_dashboard:
            self.services["dashboard"] = Service("dashboard", [sys.executable, "-u", "-m", "kripto_bot.app.dashboard"],
                                                 log_dir, DASHBOARD_COLOR, on_exit)

    def _desired(self):
        try:
            items = instances.load_all(self.instances_path)
        except Exception as e:
            print(f"[supervisor] can't read instances: {e}")
            return None
        return {n: i for n, i in items.items() if i.enabled}

    def reconcile(self):
        """Match running processes to the enabled instances."""
        desired = self._desired()
        if desired is None:
            return
        self.blocked = {}
        for name, inst in desired.items():
            if inst.mode == "testnet" and not (config.BINANCE_TESTNET_API_KEY and config.BINANCE_TESTNET_API_SECRET):
                self.blocked[name] = "testnet API keys are missing in .env"
                continue
            svc = self.services.get(name)
            if svc is None:
                color = PALETTE[len(self.services) % len(PALETTE)]
                self.services[name] = Service(name, instance_command(inst), self.log_dir, color, self.on_exit)
            elif inst.restart_requested_at and svc.started and \
                    pd.Timestamp(inst.restart_requested_at).timestamp() > svc.started:
                svc.emit("restart requested from the dashboard")
                svc.stop()
                svc.next_start = 0.0
        for name in [n for n in self.services if n != "dashboard" and n not in desired]:
            self.services.pop(name).stop()

    def write_status(self):
        data = {"updated_at": pd.Timestamp.now(tz="UTC").isoformat(), "supervisor_pid": os.getpid(),
                "services": {n: s.status() for n, s in self.services.items()},
                "blocked": self.blocked}
        tmp = f"{self.status_file}.tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.status_file)

    def tick(self):
        now = time.time()
        if now - self.last_reload >= RELOAD_EVERY_S:
            self.reconcile()
            self.last_reload = now
        for s in list(self.services.values()):
            s.check()
        if self.watcher:
            self.watcher.poll(self.blocked)
        self.write_status()

    def run(self, poll_s=1.0, should_stop=lambda: False):
        if self.watcher:
            self.reconcile()
            self.watcher.started([n for n in self.services if n != "dashboard"])
        try:
            while not should_stop():
                self.tick()
                time.sleep(poll_s)
        finally:
            for s in self.services.values():
                s.stop()
            if os.path.exists(self.status_file):
                os.remove(self.status_file)
            if self.watcher:
                self.watcher.stopped()
                self.watcher.sender.flush()


def _raise_interrupt(*_):
    raise KeyboardInterrupt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-dashboard", action="store_true")
    ap.add_argument("--keep-awake", action="store_true",
                    help="macOS: keep the computer from idle sleeping while the supervisor runs (on power)")
    a = ap.parse_args()
    try:
        lock = single_instance(paths.runtime("supervisor"))  # noqa: F841 (held until exit)
    except AlreadyRunning:
        raise SystemExit("❌ Another supervisor is already running (maybe the background service: "
                         "python -m kripto_bot service status).")
    if a.keep_awake and sys.platform == "darwin":
        # caffeinate ends by itself when this process exits (-w)
        subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())])
    sender = notify.from_config()
    watcher = notify.Watcher(sender) if sender.enabled else None
    sup = Supervisor(with_dashboard=not a.no_dashboard, watcher=watcher)
    print(f"▶️  Supervisor started (logs in {LOG_DIR}). Instances follow runtime/instances.json; "
          "Ctrl+C stops everything.")
    print("🔔 Telegram notifications on.\n" if watcher else
          "🔕 Telegram notifications off (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set in .env).\n")
    signal.signal(signal.SIGTERM, _raise_interrupt)   # `kill` stops cleanly too
    try:
        sup.run()
    except KeyboardInterrupt:
        print("\n⏹  Stopping...")


if __name__ == "__main__":
    main()
