# run_all.py
"""Start the bot's long-running services from one terminal.

Services: paper (run_realtime.py), testnet (run_testnet.py) and dashboard
(dashboard.py). Output is shown with a [service] prefix and also written to
logs/<service>.log. A service that exits is restarted after a short pause
(doubling up to 5 minutes if it keeps failing). Ctrl+C stops everything.

    python run_all.py                     # paper + dashboard (+ testnet if its keys are set)
    python run_all.py paper dashboard     # only these
"""
import argparse
import os
import signal
import subprocess
import sys
import threading
import time

import config

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")
SERVICES = {
    "paper": "run_realtime.py",
    "testnet": "run_testnet.py",
    "dashboard": "dashboard.py",
}
COLORS = {"paper": "\033[36m", "testnet": "\033[35m", "dashboard": "\033[33m"}
RESET = "\033[0m"
MIN_BACKOFF_S, MAX_BACKOFF_S = 5, 300
STABLE_AFTER_S = 600       # a run this long resets the backoff


def default_services():
    names = ["paper", "dashboard"]
    if config.BINANCE_TESTNET_API_KEY and config.BINANCE_TESTNET_API_SECRET:
        names.insert(1, "testnet")
    return names


class Service:
    def __init__(self, name, cmd, log_dir=LOG_DIR, color=True):
        self.name, self.cmd = name, cmd
        self.prefix = f"{COLORS.get(name, '') if color else ''}[{name}]{RESET if color else ''}"
        os.makedirs(log_dir, exist_ok=True)
        self.log = open(os.path.join(log_dir, f"{name}.log"), "a", buffering=1)
        self.proc = None
        self.started = 0.0
        self.backoff = MIN_BACKOFF_S
        self.next_start = 0.0
        self.restarts = 0

    def start(self):
        env = dict(os.environ, PYTHONUNBUFFERED="1", DASHBOARD_DEBUG="0")
        self.proc = subprocess.Popen(self.cmd, cwd=HERE, env=env, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, bufsize=1,
                                     start_new_session=True)  # Ctrl+C goes to the supervisor only
        self.started = time.time()
        self.emit(f"started (pid {self.proc.pid})")
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc):
        for line in proc.stdout:
            self.emit(line.rstrip("\n"))

    def emit(self, line):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"{self.prefix} {line}", flush=True)
        self.log.write(f"{stamp} {line}\n")

    def check(self):
        """Restart the service if it has exited (with backoff)."""
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
        self.proc = None
        self.restarts += 1
        self.next_start = now + self.backoff

    def stop(self, timeout=10):
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.emit("stopped")


def supervise(services, poll_s=1.0, should_stop=lambda: False):
    for s in services:
        s.start()
    try:
        while not should_stop():
            for s in services:
                s.check()
            time.sleep(poll_s)
    finally:
        for s in services:
            s.stop()


def _raise_interrupt(*_):
    raise KeyboardInterrupt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("services", nargs="*", help=f"subset of: {', '.join(SERVICES)}")
    names = ap.parse_args().services or default_services()
    unknown = [n for n in names if n not in SERVICES]
    if unknown:
        ap.error(f"unknown service(s): {', '.join(unknown)} (choose from {', '.join(SERVICES)})")

    services = [Service(n, [sys.executable, "-u", SERVICES[n]]) for n in names]
    print(f"▶️  Starting: {', '.join(names)} (logs in {LOG_DIR}). Ctrl+C stops everything.\n")
    signal.signal(signal.SIGTERM, _raise_interrupt)   # `kill` stops cleanly too
    try:
        supervise(services)
    except KeyboardInterrupt:
        print("\n⏹  Stopping...")


if __name__ == "__main__":
    main()
