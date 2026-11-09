import json
import os
import subprocess

import pytest

from kripto_bot.llm import codex_cli
from kripto_bot.llm import provider as p

CODEX = "/opt/nvm/bin/codex"
PLAN = {"symbol": "ETH/USDT", "action": "hold"}
USAGE = {"input_tokens": 10559, "cached_input_tokens": 0, "output_tokens": 197,
         "reasoning_output_tokens": 68}


def _events(*events):
    return "\n".join(json.dumps(e) for e in events) + "\n"


DONE = _events({"type": "thread.started"}, {"type": "turn.started"},
               {"type": "item.completed", "item": {"type": "agent_message"}},
               {"type": "turn.completed", "usage": USAGE})


class FakeRun:
    """Stands in for subprocess.run: answers `--version` and `exec` like Codex."""

    def __init__(self, version="codex-cli 0.166.0", answer=json.dumps(PLAN), stdout=DONE,
                 stderr="", returncode=0, raises=None):
        self.version, self.answer, self.stdout = version, answer, stdout
        self.stderr, self.returncode, self.raises = stderr, returncode, raises
        self.execs = []

    def __call__(self, cmd, **kw):
        if cmd[1:] == ["--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=self.version + "\n", stderr="")
        self.execs.append({"cmd": cmd, **kw, "workdir_files": os.listdir(kw["cwd"]),
                           "schema": json.load(open(cmd[cmd.index("--output-schema") + 1]))})
        if self.raises:
            raise self.raises
        if self.answer is not None:
            with open(cmd[cmd.index("-o") + 1], "w") as f:
                f.write(self.answer)
        return subprocess.CompletedProcess(cmd, self.returncode, stdout=self.stdout, stderr=self.stderr)


def _codex(run, **kw):
    ticks = iter(range(0, 1000, 7))
    return codex_cli.CodexCLI("gpt-6-astra", "high", CODEX, "0.166.0", codex_home="/home/u/.codex",
                              run=run, clock=lambda: next(ticks), **kw)


def test_a_good_answer_is_parsed_with_usage_and_the_setup():
    run = FakeRun()
    r = _codex(run).complete("the prompt", {"type": "object"})
    assert r.ok and r.output == PLAN
    assert r.usage == USAGE
    assert (r.model, r.effort, r.client_version) == ("gpt-6-astra", "high", "0.166.0")
    assert r.exit_code == 0 and r.latency_sec == 7
    assert '"turn.completed"' in r.events and r.raw == json.dumps(PLAN)


def test_the_command_pins_the_model_and_turns_every_tool_off():
    run = FakeRun()
    _codex(run).complete("the prompt", {"type": "object"})
    cmd = run.execs[0]["cmd"]
    assert cmd[:2] == [CODEX, "exec"]
    for flag in ("--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--json"):
        assert flag in cmd
    assert cmd[cmd.index("-s") + 1] == "read-only"
    assert cmd[cmd.index("-m") + 1] == "gpt-6-astra"
    configs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-c"]
    assert configs == ["model_reasoning_effort=high", "web_search=disabled", "check_for_update_on_startup=false"]
    disabled = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--disable"]
    assert disabled == codex_cli.DISABLED_FEATURES
    assert {"shell_tool", "browser_use", "computer_use", "plugins", "multi_agent"} <= set(disabled)
    assert cmd[-1] == "-"


def test_the_prompt_goes_through_stdin_and_the_schema_through_a_file():
    run = FakeRun()
    _codex(run).complete("the prompt", {"type": "object", "required": ["action"]})
    call = run.execs[0]
    assert call["input"] == "the prompt"
    assert call["schema"] == {"type": "object", "required": ["action"]}
    assert call["timeout"] == 180


def test_the_working_directory_is_empty_and_removed_afterwards():
    run = FakeRun()
    _codex(run).complete("the prompt", {})
    call = run.execs[0]
    assert call["workdir_files"] == []
    assert call["cmd"][call["cmd"].index("-C") + 1] == call["cwd"]
    assert not os.path.exists(call["cwd"])


def test_the_bots_keys_never_reach_codex(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret")
    monkeypatch.setenv("HOME", "/home/u")
    run = FakeRun()
    _codex(run).complete("the prompt", {})
    env = run.execs[0]["env"]
    assert set(env) <= {"HOME", "TMPDIR", "LANG", "PATH", "CODEX_HOME"}
    assert "secret" not in env.values()
    assert env["PATH"].split(":")[0] == "/opt/nvm/bin"
    assert env["CODEX_HOME"] == "/home/u/.codex"


def test_another_version_answers_nothing_and_runs_nothing():
    run = FakeRun(version="codex-cli 0.167.0")
    r = _codex(run).complete("the prompt", {})
    assert r.kind == p.VERSION_MISMATCH and r.client_version == "0.167.0"
    assert run.execs == []


def test_a_missing_codex_is_a_version_mismatch():
    def run(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    r = _codex(run).complete("the prompt", {})
    assert r.kind == p.VERSION_MISMATCH and r.client_version == ""


def test_timeout():
    run = FakeRun(raises=subprocess.TimeoutExpired("codex", 180, output=b"partial", stderr=b"slow"))
    r = _codex(run).complete("the prompt", {})
    assert r.kind == p.TIMEOUT and r.events == "partial" and r.stderr == "slow"


def test_a_failed_run_is_a_process_error():
    stdout = _events({"type": "turn.started"}, {"type": "error", "message": "stream disconnected"})
    r = _codex(FakeRun(stdout=stdout, returncode=1, answer=None)).complete("the prompt", {})
    assert r.kind == p.PROCESS_ERROR and r.exit_code == 1


@pytest.mark.parametrize("message", [
    "You've hit your usage limit. Try again in 3 hours.",
    "unexpected status 429 Too Many Requests",
])
def test_a_usage_limit_is_recognised(message):
    stdout = _events({"type": "turn.started"}, {"type": "turn.failed", "error": {"message": message}})
    r = _codex(FakeRun(stdout=stdout, returncode=1, answer=None)).complete("the prompt", {})
    assert r.kind == p.USAGE_LIMIT


def test_harmless_rate_limit_logs_do_not_fail_a_good_run():
    stderr = "ERROR codex: failed to fetch codex rate limits: request timed out\n"
    r = _codex(FakeRun(stderr=stderr)).complete("the prompt", {})
    assert r.ok


def test_a_passing_error_event_does_not_fail_a_completed_turn():
    stdout = _events({"type": "error", "message": "Reconnecting... 1/5"}, {"type": "turn.completed", "usage": USAGE})
    r = _codex(FakeRun(stdout=stdout)).complete("the prompt", {})
    assert r.ok


def test_a_run_without_a_completed_turn_is_a_process_error():
    r = _codex(FakeRun(stdout=_events({"type": "turn.started"}))).complete("the prompt", {})
    assert r.kind == p.PROCESS_ERROR


@pytest.mark.parametrize("answer", [None, "", "not json", "[1, 2]"])
def test_an_answer_that_is_not_a_json_object_is_invalid(answer):
    r = _codex(FakeRun(answer=answer)).complete("the prompt", {})
    assert r.kind == p.INVALID_OUTPUT


class Scripted(p.Provider):
    def __init__(self, *kinds):
        self.kinds = list(kinds)

    def complete(self, prompt, schema):
        return p.LLMResult(self.kinds.pop(0))


@pytest.mark.parametrize("kind", [p.TIMEOUT, p.PROCESS_ERROR])
def test_a_timeout_or_process_error_is_retried_once_after_a_minute(kind):
    waits = []
    attempts = p.call(Scripted(kind, p.OK), "x", {}, sleep=waits.append)
    assert [a.kind for a in attempts] == [kind, p.OK]
    assert [a.retry for a in attempts] == [False, True]
    assert waits == [60]


def test_a_second_failure_ends_the_call():
    attempts = p.call(Scripted(p.TIMEOUT, p.TIMEOUT, p.OK), "x", {}, sleep=lambda s: None)
    assert [a.kind for a in attempts] == [p.TIMEOUT, p.TIMEOUT]


@pytest.mark.parametrize("kind", [p.OK, p.USAGE_LIMIT, p.INVALID_OUTPUT, p.VERSION_MISMATCH])
def test_other_results_are_not_retried(kind):
    waits = []
    attempts = p.call(Scripted(kind), "x", {}, sleep=waits.append)
    assert [a.kind for a in attempts] == [kind] and waits == []
