# kripto_bot/llm/provider.py
"""What the v11 analyst needs from a language model, independent of how it is reached.

A provider takes a prompt and a JSON schema and returns one LLMResult. Only a result of kind
OK carries an answer; every other kind counts as `hold` for the analyst. The retry rule of
docs/v11-ai-analyst.md lives in call(), so every provider follows it.
"""
import time
from dataclasses import dataclass, field

OK = "ok"
TIMEOUT = "timeout"
PROCESS_ERROR = "process_error"
USAGE_LIMIT = "usage_limit"
INVALID_OUTPUT = "invalid_output"
VERSION_MISMATCH = "version_mismatch"

# Failures worth one more try: the next attempt may well work. A usage limit will not lift
# within a minute, an invalid answer is the model's answer, and a wrong client version needs
# a person.
RETRYABLE = {TIMEOUT, PROCESS_ERROR}
RETRY_DELAY_SEC = 60


@dataclass
class LLMResult:
    kind: str
    output: dict | None = None     # the parsed JSON answer, only when kind == OK
    raw: str = ""                  # the model's final message as received
    events: str = ""               # the client's event stream (JSONL) as received
    usage: dict = field(default_factory=dict)   # token counts reported by the client
    exit_code: int | None = None
    stderr: str = ""
    latency_sec: float = 0.0
    model: str = ""
    effort: str = ""
    client_version: str = ""
    retry: bool = False            # True for the second attempt of a call

    @property
    def ok(self):
        return self.kind == OK


class Provider:
    """One model setup (model and effort fixed), one independent request per complete()."""

    def complete(self, prompt: str, schema: dict) -> LLMResult:
        raise NotImplementedError


def call(provider, prompt, schema, sleep=time.sleep, delay=RETRY_DELAY_SEC):
    """Every attempt of one call, oldest first; the last one decides.

    A timeout or process error is retried once after `delay` seconds. Both attempts are
    returned so both can be recorded.
    """
    first = provider.complete(prompt, schema)
    if first.kind not in RETRYABLE:
        return [first]
    sleep(delay)
    second = provider.complete(prompt, schema)
    second.retry = True
    return [first, second]
