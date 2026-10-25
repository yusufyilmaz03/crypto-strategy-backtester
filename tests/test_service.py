import plistlib
import subprocess

import pytest

from kripto_bot.tools import service


def test_plist_runs_the_supervisor_and_restarts_after_crashes(tmp_path):
    p = service.plist(python="/venv/bin/python", root="/proj")
    assert p["ProgramArguments"] == ["/venv/bin/python", "-u", "-m", "kripto_bot", "run", "--keep-awake", "--quiet"]
    assert p["WorkingDirectory"] == "/proj" and p["KeepAlive"] == {"SuccessfulExit": False}
    assert p["StandardOutPath"] == "/proj/runtime/logs/supervisor.log"
    plistlib.dumps(p)                                   # serializable


def test_install_and_uninstall_call_launchctl(tmp_path):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 113 if cmd[1] == "print" else 0, "", "")  # 113: not loaded

    path = str(tmp_path / "agent.plist")
    service.install(run=run, path=path, sleep=lambda s: None)
    assert plistlib.load(open(path, "rb"))["Label"] == service.LABEL
    assert calls[-1][:2] == ["launchctl", "bootstrap"] and calls[-1][-1] == path
    service.restart(run=run, path=path)
    assert calls[-1][:3] == ["launchctl", "kickstart", "-k"]
    service.uninstall(run=run, path=path)
    assert calls[-1][:2] == ["launchctl", "bootout"]
    import os
    assert not os.path.exists(path)


def test_install_waits_for_the_old_service_to_stop(tmp_path):
    calls, prints = [], iter([0, 0, 0, 113])          # still loaded three times after bootout, then gone

    def run(cmd, **kw):
        calls.append(cmd[1])
        code = next(prints) if cmd[1] == "print" else 0
        return subprocess.CompletedProcess(cmd, code, "", "")

    slept = []
    service.install(run=run, path=str(tmp_path / "agent.plist"), sleep=slept.append)
    assert calls == ["bootout", "print", "print", "print", "print", "bootstrap"] and len(slept) == 3

    always = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", "")  # noqa: E731
    with pytest.raises(SystemExit, match="still stopping"):
        service.install(run=always, path=str(tmp_path / "agent.plist"), sleep=lambda s: None, timeout=-1)
