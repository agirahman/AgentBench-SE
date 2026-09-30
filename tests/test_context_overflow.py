"""A context overflow must be handled, not retried and not reported as a failure.

Why this exists
---------------

At a 200-turn allowance (the reference scale -- SWE-bench Pro 200, mini-SWE-agent
250) every turn re-sends the whole conversation, so the prompt grows within an act.
Measured on EXP-20260929-022 (pool 100), the heaviest act collected ~166k chars of
tool output across 108 calls. The pool is split across acts, but a single act at
200 turns can accumulate enough context to hit the model's window.

Before this, there was NO context handling anywhere in the codebase -- no window
check, no overflow detection, no trimming. The consequence would have been:

* the request fails with a context error,
* ``call_with_retry`` treats it as a transient provider error and RETRIES it,
* the conversation only grows, so the retry fails again and is billed again,
* the act is marked FAILED -- reporting a context limit as a strategy failure,
  which is precisely the confound the whole budget design exists to remove.

These tests pin the intended behaviour: detect it, stop the act, and still return
what was established.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from providers import tool_loop  # noqa: E402


class _FakeFunction:
    def __init__(self, name="read_file", arguments='{"path": "a.py"}'):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, idx=0):
        self.id = f"call-{idx}"
        self.function = _FakeFunction()


class _FakeMessage:
    def __init__(self, tool_calls=None, content=""):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, msg, finish_reason="stop"):
        self.message = msg
        self.finish_reason = finish_reason


class _FakeUsage:
    def __init__(self, prompt=10, completion=5):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = prompt + completion


class _FakeResponse:
    def __init__(self, msg, usage=None):
        self.choices = [_FakeChoice(msg)]
        self.usage = usage or _FakeUsage()


class _OverflowError(Exception):
    """Stands in for the provider's context error (wording matters)."""


class _OverflowingCompletions:
    """Works normally for N turns, then rejects with a context error."""

    def __init__(self, overflow_after: int, message="maximum context length exceeded"):
        self.overflow_after = overflow_after
        self.message = message
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls > self.overflow_after:
            raise _OverflowError(self.message)
        return _FakeResponse(_FakeMessage([_FakeToolCall(self.calls)]))


class _FakeClient:
    def __init__(self, completions):
        self.chat = type("Chat", (), {})()
        self.chat.completions = completions


def _run(client, **kwargs):
    kwargs.setdefault("max_tool_turns", 50)
    return tool_loop.run_tool_loop(
        client,
        model="fake",
        prompt="fix it",
        role="executor",
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
        repo_root=None,
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _fast_tool(monkeypatch):
    monkeypatch.setattr(tool_loop, "execute_tool", lambda name, args: "TOOL OUTPUT")
    monkeypatch.setattr(tool_loop.Config, "TOOL_OUTPUT_MAX_CHARS", 2000)


# --------------------------------------------------------------- detection
def test_context_overflow_is_recognised_from_the_message():
    """Providers disagree on status codes; the wording is the common signal."""
    for text in (
        "This model's maximum context length is 128000 tokens",
        "context_length_exceeded",
        "prompt is too long: 200000 tokens",
        "Input is too long for the model",
        "Please reduce the length of the messages",
        "too many tokens",
    ):
        assert tool_loop._is_context_overflow(Exception(text)), text


def test_unrelated_errors_are_not_mistaken_for_context_overflow():
    """A 502 or a rate limit must keep propagating to the normal error path.

    Swallowing those here would turn real infrastructure failures into silently
    truncated runs, which is a worse bug than the one being fixed.
    """
    for text in (
        "Error code: 502 - Bad Gateway",
        "Error code: 429 - rate limit exceeded",
        "Connection reset by peer",
        "Error code: 401 - invalid api key",
    ):
        assert not tool_loop._is_context_overflow(Exception(text)), text


# ------------------------------------------------------------ loop behaviour
def test_the_act_stops_instead_of_retrying_forever():
    """Retrying an overflow is pointless: the conversation only grows.

    The act must end at the point of failure, not spin through the retry schedule
    failing identically each time and paying for the tokens again. The count is the
    assertion that matters: without ``fatal_on`` the overflow is retried
    MAX_RETRIES times before the caller's handler ever sees it.
    """
    completions = _OverflowingCompletions(overflow_after=3)
    result = _run(_FakeClient(completions))

    # 3 normal turns, then the overflow, then ONE final-answer request (which also
    # overflows). Anything above 5 means the overflow was retried.
    assert completions.calls <= 5, (
        f"the overflow was retried instead of stopping the act "
        f"({completions.calls} requests)"
    )
    assert result.truncated is True, "an act stopped by the context limit is truncated"


def test_a_context_stop_is_labelled_by_its_cause():
    """The record must say WHY the act ended.

    Reporting a context limit as "hit max_tool_turns" would send a reader looking
    for a budget problem when the model simply ran out of window.
    """
    completions = _OverflowingCompletions(overflow_after=2)
    result = _run(_FakeClient(completions))

    bounds = [e for e in result.trajectory if e["type"] == "bound_reached"]
    assert bounds, "the stop must appear in the trajectory"
    assert bounds[0]["stop_reason"] == "context_limit", bounds[0]


def test_the_act_still_returns_something(monkeypatch):
    """An act that returns nothing scores as a total failure.

    When even the trimmed final-answer request overflows, the result must carry the
    work already done (the recorded tool calls) rather than raising out of the loop
    and losing the act entirely.
    """
    completions = _OverflowingCompletions(overflow_after=2)
    result = _run(_FakeClient(completions))

    assert result is not None
    assert result.tool_calls, "the calls already executed must survive"
    assert result.finish_reason == "context_limit"
    assert result.truncated is True


def test_trimming_shortens_the_oldest_outputs_and_keeps_recent_ones():
    """Trimming must free room without discarding what the agent just learned.

    The decision about what to do next depends on what was read most recently, so
    the newest results are protected and the oldest are cut.
    """
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    for i in range(10):
        messages.append({"role": "assistant", "content": f"a{i}"})
        messages.append({"role": "tool", "content": "Z" * 4000})

    before = sum(len(m.get("content") or "") for m in messages)
    tool_loop._trim_messages_for_context(messages, keep_chars=2000)
    after = sum(len(m.get("content") or "") for m in messages)

    assert after < before, "trimming must actually reduce the prompt"
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    # The three most recent are untouched.
    for msg in tool_msgs[-3:]:
        assert len(msg["content"]) == 4000, "recent results must stay intact"
    # The oldest were cut.
    assert len(tool_msgs[0]["content"]) < 4000


def test_trimming_is_a_noop_when_there_is_nothing_to_trim():
    """A conversation with no oversized tool output must not be corrupted."""
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    tool_loop._trim_messages_for_context(messages, keep_chars=2000)
    assert len(messages) == 2
