# run_all.py
"""Run the dashboard and every enabled bot instance from one terminal.

The supervisor follows instances.json: an instance that is enabled (from the
dashboard or the file) gets its own process (run_realtime.py or run_testnet.py
--instance NAME), a disabled or deleted one is stopped, and a restart requested
from the dashboard restarts it. Output is shown with a [name] prefix and written to
logs/<name>.log. A process that exits is restarted after a pause (doubling up to
5 minutes while it keeps failing). Current states go to status.json for the
dashboard. Ctrl+C stops everything.

    python run_all.py                  # dashboard + enabled instances
    python run_all.py --no-dashboard   # instances only
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

import config
import instances

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")
STATUS_FILE = os.path.join(HERE, "status.json")
PALETTE = ["\033[36m", "\033[35m", "\033[32m", "\033[34m", "\033[91m", "\033[96m"]
DASHBOARD_COLOR = "\033[33m"
RESET = "\033[0m"
MIN_BACKOFF_S, MAX_BACKOFF_S = 5, 300
STABLE_AFTER_S = 600       # a run this long resets the backoff
RELOAD_EVERY_S = 2.0


class Service:
    def __init__(self, name, cmd, log_dir=LOG_DIR, color=""):
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

    def start(self):
        env = dict(os.environ, PYTHONUNBUFFERED="1", DASHBOARD_DEBUG="0")
        self.proc = subprocess.Popen(self.cmd, cwd=HERE, env=env, stdout=subprocess.PIPE,
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
    script = "run_realtime.py" if inst.mode == "paper" else "run_testnet.py"
    return [sys.executable, "-u", script, "--instance", inst.name]


class Supervisor:
    def __init__(self, with_dashboard=True, instances_path=None, log_dir=LOG_DIR, status_file=STATUS_FILE):
        self.instances_path = instances_path
        self.log_dir = log_dir
        self.status_file = status_file
        self.services = {}          # service name -> Service
        self.blocked = {}           # instance name -> reason it can't start
        self.last_reload = 0.0
        if with_dashboard:
            self.services["dashboard"] = Service("dashboard", [sys.executable, "-u", "dashboard.py"],
                                                 log_dir, DASHBOARD_COLOR)

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
                self.services[name] = Service(name, instance_command(inst), self.log_dir, color)
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
        self.write_status()

    def run(self, poll_s=1.0, should_stop=lambda: False):
        try:
            while not should_stop():
                self.tick()
                time.sleep(poll_s)
        finally:
            for s in self.services.values():
                s.stop()
            if os.path.exists(self.status_file):
                os.remove(self.status_file)


def _raise_interrupt(*_):
    raise KeyboardInterrupt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-dashboard", action="store_true")
    a = ap.parse_args()
    sup = Supervisor(with_dashboard=not a.no_dashboard)
    print(f"▶️  Supervisor started (logs in {LOG_DIR}). Instances follow instances.json; "
          "Ctrl+C stops everything.\n")
    signal.signal(signal.SIGTERM, _raise_interrupt)   # `kill` stops cleanly too
    try:
        sup.run()
    except KeyboardInterrupt:
        print("\n⏹  Stopping...")


if __name__ == "__main__":
    main()
