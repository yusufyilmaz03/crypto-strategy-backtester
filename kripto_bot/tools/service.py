# kripto_bot/tools/service.py
"""Run the supervisor as a macOS background service (launchd).

    python -m kripto_bot service install     # start now and at every login
    python -m kripto_bot service status
    python -m kripto_bot service restart     # after a code update
    python -m kripto_bot service uninstall   # stop and remove

The service runs `python -m kripto_bot run --keep-awake --quiet`: the supervisor
keeps the Mac from idle sleeping while it is on power (closing a laptop's lid still
sleeps it). launchd restarts the supervisor if it crashes. The supervisor's own
messages and the services' start/stop/exit events go to runtime/logs/supervisor.log;
each service's output is in runtime/logs/<name>.log. Stop a manually started
supervisor first: only one can run at a time. After an update that changes this
file, run `install` again to rewrite the service definition.
"""
import argparse
import os
import plistlib
import subprocess
import sys

from kripto_bot import paths

LABEL = "com.kripto-bot.supervisor"
PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")


def plist(python=None, root=None):
    python = python or sys.executable
    root = root or paths.ROOT
    log = os.path.join(root, "runtime", "logs", "supervisor.log")
    return {
        "Label": LABEL,
        "ProgramArguments": [python, "-u", "-m", "kripto_bot", "run", "--keep-awake", "--quiet"],
        "WorkingDirectory": root,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},     # restart after a crash, not after a clean stop
        "ThrottleInterval": 30,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }


def _launchctl(*args, run=subprocess.run):
    return run(["launchctl", *args], capture_output=True, text=True)


def domain():
    return f"gui/{os.getuid()}"


def install(run=subprocess.run, path=PLIST):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.makedirs(paths.LOG_DIR, exist_ok=True)
    _launchctl("bootout", f"{domain()}/{LABEL}", run=run)           # replace an older version
    with open(path, "wb") as f:
        plistlib.dump(plist(), f)
    r = _launchctl("bootstrap", domain(), path, run=run)
    if r.returncode != 0:
        raise SystemExit(f"launchctl bootstrap failed: {r.stderr.strip() or r.stdout.strip()}")
    print(f"✅ Service installed and started ({path}).\n   Log: {plist()['StandardOutPath']}\n"
          "   Panel: http://127.0.0.1:5002")


def uninstall(run=subprocess.run, path=PLIST):
    _launchctl("bootout", f"{domain()}/{LABEL}", run=run)
    if os.path.exists(path):
        os.remove(path)
    print("⏹  Service stopped and removed.")


def restart(run=subprocess.run, path=PLIST):
    if not os.path.exists(path):
        raise SystemExit("Service not installed (python -m kripto_bot service install).")
    r = _launchctl("kickstart", "-k", f"{domain()}/{LABEL}", run=run)   # -k: stop the running one first
    if r.returncode != 0:
        raise SystemExit(f"launchctl kickstart failed: {r.stderr.strip() or r.stdout.strip()}")
    print("🔄 Service restarted.")


def status(run=subprocess.run, path=PLIST):
    if not os.path.exists(path):
        print("Service not installed.")
        return
    r = _launchctl("print", f"{domain()}/{LABEL}", run=run)
    if r.returncode != 0:
        print("Service installed but not loaded (try: python -m kripto_bot service install).")
        return
    fields = {}
    for line in r.stdout.splitlines():
        key, sep, value = line.strip().partition(" = ")
        if sep and key in ("state", "pid", "last exit code", "runs"):
            fields.setdefault(key, value)
    print("Service " + ", ".join(f"{k}: {v}" for k, v in fields.items()))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["install", "uninstall", "status", "restart"])
    a = ap.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("The service command is for macOS (launchd). On a server use Docker, see the README.")
    {"install": install, "uninstall": uninstall, "status": status, "restart": restart}[a.action]()


if __name__ == "__main__":
    main()
