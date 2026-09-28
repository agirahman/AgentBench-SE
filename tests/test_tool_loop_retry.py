"""Retry must not restart the tool-calling loop.

Regression test for the EXP-20260928-001 bug (django-11019, direct):

    segment 0 : 45 tool calls -> request timeout
    segment 1 : 16 tool calls -> request timeout
    segment 2 : 38 tool calls -> finished
    TOTAL     : 99 tool calls  (budget was 60)

``@with_retry`` wrapped the whole ``generate_with_tools()``, so one HTTP timeout
restarted the conversation from scratch: the exploration already gathered was
thrown away AND the loop was handed a fresh tool-turn budget. With
MAX_RETRIES=3, a single act could spend 180 turns instead of 60 — which silently
invalidated the "equal budget" property that makes the three strategies
comparable.

The tests here drive the real loop with a fake client and assert:

1. a timeout on turn N is retried in place, so the conversation continues and
   the tool-turn budget is NOT extended;
2. an empty (no tool call, no text) response is retried in place;
3. tokens from a retried attempt are still counted — it was paid for;
4. tool output is truncated head+tail, so late turns stay small.

No network call is made: the fake client raises or returns canned objects.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from providers import tool_loop  # noqa: E402


# ---------------------------------------------------------------------------
# Fake OpenAI-compatible client
# ---------------------------------------------------------------------------

class _FakeFunction:
    def __init__(self, name="read_file", arguments="{}"):
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
    def __init__(self, msg):
        self.message = msg


class _FakeUsage:
    def __init__(self, prompt=10, completion=5):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = prompt + completion


class _FakeResponse:
    def __init__(self, msg, usage=None):
        self.choices = [_FakeChoice(msg)]
        self.usage = usage or _FakeUsage()


class _ScriptedCompletions:
    """Returns a scripted response per call; ``Exception`` entries are raised.

    ``raise_after`` counts HTTP attempts, not turns, so a script can express
    "the first attempt at turn 2 times out, the retry succeeds".
    """

    def __init__(self, script, recorder=None):
        self._script = list(script)
        self.calls = 0
        self.messages_seen = recorder if recorder is not None else []

    def create(self, **kwargs):
        self.calls += 1
        self.messages_seen.append([dict(m) for m in kwargs["messages"]])
        if not self._script:
            raise AssertionError(
                "fake client exhausted: the loop made more requests than the "
                "script provides (a retry loop would show up here)"
            )
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeClient:
    def __init__(self, script):
        self.chat = type("Chat", (), {})()
        self.chat.completions = _ScriptedCompletions(script)


def _tool_response(idx=0):
    return _FakeResponse(_FakeMessage([_FakeToolCall(idx)]))


def _final_response(text="done"):
    return _FakeResponse(_FakeMessage(None, content=text))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Retries must not actually wait in the test suite."""
    import evaluation.retry as retry_mod

    monkeypatch.setattr(retry_mod.time, "sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def _fast_tool(monkeypatch):
    """Execute no real tool; return a marker the test can look for."""
    monkeypatch.setattr(tool_loop, "execute_tool", lambda name, args: "TOOL_OUT")


def _run(client, monkeypatch, *, max_tool_turns=5, role="direct", retries=3):
    # Patch BOTH references: tool_loop and evaluation.retry each did their own
    # `from config import Config`, and another test (test_response_utils)
    # reloads the config module, so the two can point at different class
    # objects. Patching only one would silently leave the other at the value
    # inherited from the ambient shell.
    import evaluation.retry as retry_mod

    for cfg in (tool_loop.Config, retry_mod.Config):
        monkeypatch.setattr(cfg, "MAX_RETRIES", retries)
        monkeypatch.setattr(cfg, "RATE_LIMIT_BACKOFF_BASE", 0.0)
        monkeypatch.setattr(cfg, "RATE_LIMIT_BACKOFF_MAX", 0.0)
        monkeypatch.setattr(cfg, "TOOL_OUTPUT_MAX_CHARS", 2000)
    return tool_loop.run_tool_loop(
        client,
        model="fake",
        prompt="fix it",
        role=role,
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
        max_tool_turns=max_tool_turns,
        repo_root=None,
    )


# ---------------------------------------------------------------------------
# 1. A timeout must not restart the loop
# ---------------------------------------------------------------------------

def test_timeout_on_a_turn_does_not_restart_the_loop(monkeypatch):
    """Turn 1 succeeds, turn 2 times out once, the retry of turn 2 finishes.

    Under the old @with_retry-on-the-whole-loop behaviour, the timeout discarded
    turn 1's tool call and started a brand-new conversation: the client would
    have seen 3 requests (1 + restart + restart) and the budget would have been
    reissued. Now the loop must make exactly 3 requests: turn 1, the failed
    attempt at turn 2, and the successful retry of turn 2 — with turn 1's tool
    call still in the conversation.
    """
    client = _FakeClient([
        _tool_response(0),          # turn 1: a tool call
        TimeoutError("read timed out"),  # turn 2, attempt 1
        _final_response("done"),     # turn 2, attempt 2 (retry) -> final answer
    ])

    result = _run(client, monkeypatch, max_tool_turns=5)

    assert result.response == "done"
    assert len(result.tool_calls) == 1, "turn 1's work must survive the timeout"
    assert result.api_turns == 2, "the timeout must not buy extra turns"

    # The retried turn 2 must carry turn 1's tool result: the exploration was
    # kept, not restarted.
    last_messages = client.chat.completions.messages_seen[-1]
    assert any(m.get("role") == "tool" and m.get("content") == "TOOL_OUT"
               for m in last_messages), "the retry lost the earlier tool result"


def test_retries_never_buy_extra_tool_turns(monkeypatch):
    """HTTP requests may multiply; LOGICAL turns may not.

    This is the property the old design broke: a retry handed the loop a fresh
    budget, so one act could spend max_tool_turns * MAX_RETRIES turns (180 for a
    budget of 60). Here every turn times out once and then succeeds: 6 HTTP
    requests, but still exactly 3 turns — the budget the strategy granted.
    """
    client = _FakeClient([
        TimeoutError("read timed out"), _tool_response(0),  # turn 1
        TimeoutError("read timed out"), _tool_response(1),  # turn 2
        TimeoutError("read timed out"), _final_response("done"),  # turn 3
    ])

    result = _run(client, monkeypatch, max_tool_turns=3)

    assert client.chat.completions.calls == 6, "3 turns, each retried once"
    assert result.api_turns == 3, (
        f"retries must not enlarge the budget: got {result.api_turns} turns "
        f"for a budget of 3"
    )
    assert len(result.tool_calls) == 2, "both tool calls must survive their retries"


def test_exhausted_retries_still_raise(monkeypatch):
    """When a turn cannot be recovered, the error must surface — not be hidden.

    The runner records it into ExperimentResult.evaluation.error, and the run
    can be resumed. Swallowing it would produce a silent empty patch.
    """
    client = _FakeClient([TimeoutError("read timed out")] * 50)

    with pytest.raises(TimeoutError):
        _run(client, monkeypatch, max_tool_turns=2, retries=3)

    assert client.chat.completions.calls == 3, (
        f"one turn x 3 attempts, then it must give up; got "
        f"{client.chat.completions.calls} requests"
    )


# ---------------------------------------------------------------------------
# 2. An empty response is retried in place, not by restarting
# ---------------------------------------------------------------------------

def test_empty_response_is_retried_in_place(monkeypatch):
    """A no-tool-call/no-text response is a dead end; resend that one request."""
    client = _FakeClient([
        _tool_response(0),                              # turn 1
        _FakeResponse(_FakeMessage(None, content="")),  # turn 2: empty
        _final_response("done"),                        # turn 2 retried
    ])

    result = _run(client, monkeypatch, max_tool_turns=5)

    assert result.response == "done"
    assert len(result.tool_calls) == 1
    assert result.api_turns == 2


def test_tool_call_with_empty_text_is_not_retried(monkeypatch):
    """An empty-content response WITH tool calls is normal tool calling.

    Retrying it would waste requests and could spin: the model is mid-work.
    """
    client = _FakeClient([
        _tool_response(0),          # empty text, but a tool call -> normal
        _final_response("done"),
    ])

    result = _run(client, monkeypatch, max_tool_turns=5)

    assert result.response == "done"
    assert result.api_turns == 2
    assert client.chat.completions.calls == 2, "no retry should have happened"


# ---------------------------------------------------------------------------
# 3. A retried request was still paid for
# ---------------------------------------------------------------------------

def test_tokens_from_a_failed_attempt_are_counted(monkeypatch):
    """The empty response was billed; dropping its usage would understate cost."""
    empty = _FakeResponse(_FakeMessage(None, content=""), usage=_FakeUsage(100, 50))
    client = _FakeClient([empty, _final_response("done")])

    result = _run(client, monkeypatch, max_tool_turns=5)

    # 2 requests: the empty one (100+50) and the good one (10+5).
    assert result.usage["prompt_tokens"] == 110
    assert result.usage["completion_tokens"] == 55
    assert result.usage["total_tokens"] == 165


# ---------------------------------------------------------------------------
# 4. Tool output is capped head+tail
# ---------------------------------------------------------------------------

def test_large_tool_output_is_truncated_keeping_head_and_tail(monkeypatch):
    """The tail carries the error/diff summary, so it must not be dropped."""
    big = "HEAD" + ("x" * 5000) + "TAIL"

    monkeypatch.setattr(tool_loop, "execute_tool", lambda name, args: big)
    monkeypatch.setattr(tool_loop.Config, "TOOL_OUTPUT_MAX_CHARS", 200)

    client = _FakeClient([_tool_response(0), _final_response("done")])
    _run(client, monkeypatch, max_tool_turns=5)

    tool_messages = [
        m for m in client.chat.completions.messages_seen[-1]
        if m.get("role") == "tool"
    ]
    assert tool_messages, "the tool result should be in the conversation"
    content = tool_messages[0]["content"]
    assert len(content) < len(big), "the output should have been capped"
    assert content.startswith("HEAD"), "head must be kept"
    assert content.endswith("TAIL"), "tail must be kept"
    assert "chars omitted" in content


def test_small_tool_output_is_untouched(monkeypatch):
    monkeypatch.setattr(tool_loop, "execute_tool", lambda name, args: "small")
    monkeypatch.setattr(tool_loop.Config, "TOOL_OUTPUT_MAX_CHARS", 2000)

    client = _FakeClient([_tool_response(0), _final_response("done")])
    _run(client, monkeypatch, max_tool_turns=5)

    tool_messages = [
        m for m in client.chat.completions.messages_seen[-1]
        if m.get("role") == "tool"
    ]
    assert tool_messages[0]["content"] == "small"
