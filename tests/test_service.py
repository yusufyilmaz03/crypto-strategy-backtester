import plistlib
import subprocess

from kripto_bot.tools import service


def test_plist_runs_the_supervisor_and_restarts_after_crashes(tmp_path):
    p = service.plist(python="/venv/bin/python", root="/proj")
    assert p["ProgramArguments"] == ["/venv/bin/python", "-u", "-m", "kripto_bot", "run", "--keep-awake"]
    assert p["WorkingDirectory"] == "/proj" and p["KeepAlive"] == {"SuccessfulExit": False}
    assert p["StandardOutPath"] == "/proj/runtime/logs/supervisor.log"
    plistlib.dumps(p)                                   # serializable


def test_install_and_uninstall_call_launchctl(tmp_path):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    path = str(tmp_path / "agent.plist")
    service.install(run=run, path=path)
    assert plistlib.load(open(path, "rb"))["Label"] == service.LABEL
    assert calls[-1][:2] == ["launchctl", "bootstrap"] and calls[-1][-1] == path
    service.uninstall(run=run, path=path)
    assert calls[-1][:2] == ["launchctl", "bootout"]
    import os
    assert not os.path.exists(path)
