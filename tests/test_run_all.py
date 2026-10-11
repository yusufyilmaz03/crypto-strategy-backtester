import sys
import time

import run_all
from run_all import Service, supervise


def _svc(tmp_path, name, code):
    return Service(name, [sys.executable, "-u", "-c", code], log_dir=str(tmp_path), color=False)


def test_output_is_prefixed_and_logged(tmp_path, capsys):
    s = _svc(tmp_path, "demo", "print('hello')")
    s.start()
    s.proc.wait(5)
    time.sleep(0.2)
    assert "[demo] hello" in capsys.readouterr().out
    assert "hello" in (tmp_path / "demo.log").read_text()


def test_crashed_service_is_restarted_with_backoff(tmp_path, monkeypatch):
    monkeypatch.setattr(run_all, "MIN_BACKOFF_S", 0)
    s = _svc(tmp_path, "crash", "raise SystemExit(3)")
    deadline = time.time() + 5
    ticks = {"n": 0}

    def stop():
        ticks["n"] += 1
        return s.restarts >= 2 or time.time() > deadline

    supervise([s], poll_s=0.05, should_stop=stop)
    assert s.restarts >= 2
    assert "exited with code 3" in (tmp_path / "crash.log").read_text()


def test_stop_terminates_running_services(tmp_path):
    s = _svc(tmp_path, "sleepy", "import time; time.sleep(60)")
    s.start()
    proc = s.proc
    s.stop(timeout=5)
    assert proc.poll() is not None


def test_default_services_follow_testnet_keys(monkeypatch):
    monkeypatch.setattr(run_all.config, "BINANCE_TESTNET_API_KEY", "")
    assert run_all.default_services() == ["paper", "dashboard"]
    monkeypatch.setattr(run_all.config, "BINANCE_TESTNET_API_KEY", "k")
    monkeypatch.setattr(run_all.config, "BINANCE_TESTNET_API_SECRET", "s")
    assert run_all.default_services() == ["paper", "testnet", "dashboard"]


def test_single_instance_lock(tmp_path):
    import pytest
    from store import AlreadyRunning, single_instance

    db = str(tmp_path / "paper.db")
    held = single_instance(db)
    code = ("import sys; sys.path.insert(0, %r); from store import single_instance, AlreadyRunning\n"
            "try:\n    single_instance(%r)\nexcept AlreadyRunning:\n    sys.exit(7)" % (run_all.HERE, db))
    import subprocess
    assert subprocess.run([sys.executable, "-c", code]).returncode == 7   # second process refused
    held.close()
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0   # free again
