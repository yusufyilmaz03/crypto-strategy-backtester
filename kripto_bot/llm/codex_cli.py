# kripto_bot/llm/codex_cli.py
"""The v11 model through Codex CLI (`codex exec`) on the user's ChatGPT plan.

Each request is one independent, non-interactive run (docs/v11-ai-analyst.md, "Model access"):
- an empty working directory, no saved session, the user's Codex config and rules ignored;
- read-only sandbox, web search off, every tool feature off, so the model can only answer;
- the answer must match the JSON schema (`--output-schema`), events come back as JSONL;
- a minimal environment: the keys the bot loads from .env never reach the process;
- the CLI version is checked before every request; another version answers nothing.
"""
import json
import os
import subprocess
import tempfile
import time

from kripto_bot.llm import provider as p

# Codex features that are on by default and would give the model tools.
DISABLED_FEATURES = [
    "shell_tool", "unified_exec", "browser_use", "browser_use_external", "computer_use",
    "plugins", "apps", "multi_agent", "image_generation", "view_image", "skill_search",
    "tool_suggest", "goals", "sleep_tool", "hooks", "in_app_browser",
]

# Only these variables are passed on. PATH is rebuilt so it holds the directory of the
# codex launcher (with nvm, `node` lives there too) and the system directories.
PASSED_ENV = ("HOME", "TMPDIR", "LANG")
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

# Error texts that mean the plan's quota is used up. Matched in the error events and stderr of
# a failed run only: Codex also logs harmless lines about fetching rate limits.
USAGE_LIMIT_MARKERS = ("usage limit", "rate limit reached", "too many requests", "429")


def usage_limited(text):
    text = text.lower()
    return any(m in text for m in USAGE_LIMIT_MARKERS)


def parse_events(jsonl):
    """(completed, usage, error messages) from Codex's JSONL events.

    `completed` is True when a turn finished; error events alone don't fail a run, because
    Codex also reports passing problems (a reconnect) that way.
    """
    completed, usage, errors = False, {}, []
    for line in jsonl.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "turn.completed":
            completed, usage = True, event.get("usage") or {}
        elif kind == "error":
            errors.append(str(event.get("message", "")))
        elif kind == "turn.failed":
            errors.append(str((event.get("error") or {}).get("message", "")))
    return completed, usage, errors


class CodexCLI(p.Provider):
    def __init__(self, model, effort, codex_path, expected_version, codex_home=None,
                 timeout=180, run=subprocess.run, clock=time.monotonic):
        self.model, self.effort = model, effort
        self.codex_path, self.expected_version = codex_path, expected_version
        self.codex_home = codex_home or os.path.expanduser("~/.codex")
        self.timeout, self.run, self.clock = timeout, run, clock

    def env(self):
        env = {k: os.environ[k] for k in PASSED_ENV if k in os.environ}
        env["PATH"] = os.path.dirname(self.codex_path) + ":" + SYSTEM_PATH
        env["CODEX_HOME"] = self.codex_home
        return env

    def version(self):
        """The installed version ("0.166.0"), or "" when codex can't be run."""
        try:
            out = self.run([self.codex_path, "--version"], capture_output=True, text=True,
                           timeout=30, env=self.env())
        except (OSError, subprocess.SubprocessError):
            return ""
        words = (out.stdout or "").split()
        return words[-1] if out.returncode == 0 and words else ""

    def command(self, workdir, schema_path, output_path):
        cmd = [self.codex_path, "exec", "--skip-git-repo-check", "--ephemeral",
               "--ignore-user-config", "--ignore-rules", "-s", "read-only", "-C", workdir,
               "-m", self.model, "-c", f"model_reasoning_effort={self.effort}",
               "-c", "web_search=disabled", "-c", "check_for_update_on_startup=false"]
        for feature in DISABLED_FEATURES:
            cmd += ["--disable", feature]
        return cmd + ["--color", "never", "--json", "--output-schema", schema_path,
                      "-o", output_path, "-"]

    def _result(self, kind, **kw):
        return p.LLMResult(kind, model=self.model, effort=self.effort, **kw)

    def complete(self, prompt, schema):
        found = self.version()
        if found != self.expected_version:
            return self._result(p.VERSION_MISMATCH, client_version=found,
                                stderr=f"codex version {found or 'unknown'}, expected {self.expected_version}")
        with tempfile.TemporaryDirectory(prefix="v11-codex-") as tmp:
            workdir = os.path.join(tmp, "work")     # stays empty: the schema and answer live beside it
            os.mkdir(workdir)
            schema_path = os.path.join(tmp, "schema.json")
            output_path = os.path.join(tmp, "answer.json")
            with open(schema_path, "w") as f:
                json.dump(schema, f)
            start = self.clock()
            try:
                proc = self.run(self.command(workdir, schema_path, output_path), input=prompt,
                                capture_output=True, text=True, timeout=self.timeout,
                                env=self.env(), cwd=workdir)
            except subprocess.TimeoutExpired as e:
                return self._result(p.TIMEOUT, client_version=found, latency_sec=self.clock() - start,
                                    events=_text(e.stdout), stderr=_text(e.stderr))
            except OSError as e:
                return self._result(p.PROCESS_ERROR, client_version=found, stderr=str(e),
                                    latency_sec=self.clock() - start)
            latency = self.clock() - start
            raw = ""
            if os.path.exists(output_path):
                with open(output_path) as f:
                    raw = f.read()
        completed, usage, errors = parse_events(proc.stdout or "")
        kw = dict(raw=raw, events=proc.stdout or "", usage=usage, exit_code=proc.returncode,
                  stderr=proc.stderr or "", latency_sec=latency, client_version=found)
        if proc.returncode != 0 or not completed:
            if usage_limited(" ".join(errors + [kw["stderr"]])):
                return self._result(p.USAGE_LIMIT, **kw)
            return self._result(p.PROCESS_ERROR, **kw)
        try:
            output = json.loads(raw)
        except ValueError:
            output = None
        if not isinstance(output, dict):
            return self._result(p.INVALID_OUTPUT, **kw)
        return self._result(p.OK, output=output, **kw)


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""
