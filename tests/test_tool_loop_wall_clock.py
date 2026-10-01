"""A single act must be bounded in WALL-CLOCK time, not just in turns and dollars.

Why this matters for a 50-issue x 3-strategy sweep (~6 hours, 150 runs):

The turn budget and the cost cap do not bound DURATION. Every HTTP request is
wrapped in a retry whose rate-limit schedule sleeps ``RATE_LIMIT_BACKOFF_BASE *
2^(n-1)`` capped at ``RATE_LIMIT_BACKOFF_MAX`` -- with the shipped defaults of 60 s
and 300 s, one request can wait 60 + 120 = 180 s before finally failing. An act of
40 turns is 41 requests, so a bad patch of rate limits turns one act into over an
hour of sleeping, and a three-act strategy into three.

Measured, not theoretical: EXP-20260929-022 django-11019/review took 5,992 s
(100 minutes) and its own summary records the 502 that ended it. The rate-limit
circuit breaker cannot help, because the backoff happens INSIDE the tool loop and
never surfaces as a failure to the runner -- so the run appears to be making
progress while it waits.

These tests drive the real loop and assert the bound actually stops it, and that a
stopped act is reported as truncated rather than passed off as a finished one.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from providers import tool_loop  # noqa: E402


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


class _SlowCompletions:
    """Every request burns real wall-clock time, like a backoff does.

    The sleep is real (a fraction of a second, not 60 s) because the guard reads
    the actual clock: patching ``time.sleep`` would make the loop instant and the
    bound could never be reached, which would test nothing.
    """

    def __init__(self, per_call_seconds: float, calls_before_final: int = 10_000):
        self.per_call = per_call_seconds
        self.calls = 0
        self.calls_before_final = calls_before_final

    def create(self, **kwargs):
        self.calls += 1
        time.sleep(self.per_call)
        if self.calls >= self.calls_before_final:
            return _FakeResponse(_FakeMessage(None, content="done"))
        return _FakeResponse(_FakeMessage([_FakeToolCall(self.calls)]))


class _FakeClient:
    def __init__(self, completions):
        self.chat = type("Chat", (), {})()
        self.chat.completions = completions


@pytest.fixture(autouse=True)
def _fast_tool(monkeypatch):
    monkeypatch.setattr(tool_loop, "execute_tool", lambda name, args, role="": "TOOL_OUT")
    monkeypatch.setattr(tool_loop.Config, "TOOL_OUTPUT_MAX_CHARS", 2000)


def _run(client, **kwargs):
    return tool_loop.run_tool_loop(
        client,
        model="fake",
        prompt="fix it",
        role="direct",
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
        repo_root=None,
        **kwargs,
    )


def test_act_stops_when_it_runs_past_its_wall_clock_bound():
    """The bound must actually end the act, not merely be recorded.

    Without it the loop would run all 40 turns at 0.05 s each; the bound is set so
    it is reached first. The assertion is that the loop stopped EARLY -- fewer
    requests than the turn budget allows -- which is the whole point.
    """
    completions = _SlowCompletions(per_call_seconds=0.05)
    client = _FakeClient(completions)

    result = _run(client, max_tool_turns=40, max_wall_seconds=0.12)

    assert completions.calls < 40, (
        f"the loop ran all {completions.calls} turns despite a 0.12 s bound"
    )
    assert result.truncated is True, (
        "an act stopped by the clock was cut off by a bound, exactly like one "
        "stopped by turns or cost; it must not be reported as a clean finish"
    )


def test_the_bound_is_checked_before_starting_a_turn():
    """A turn must not be started that the bound cannot fit.

    The guard is at the TOP of the loop, so the loop stops before issuing a
    request it cannot afford -- not after paying for it.

    The count is turns + 1: after breaking out, the loop makes ONE final-answer
    request without tools (the same wrap-up the turn and cost guards use), because
    an act that returns nothing is worse than one that returns a partial answer.
    So 3 turns fit inside the 0.12 s bound and the 4th call is the wrap-up.
    """
    completions = _SlowCompletions(per_call_seconds=0.05)
    client = _FakeClient(completions)

    _run(client, max_tool_turns=40, max_wall_seconds=0.12)

    # 3 turns at 0.05 s = 0.15 s > 0.12 s, so at most 3 turns may start; the 4th
    # request is the final-answer call. Anything beyond that means the bound was
    # consulted too late and a turn was paid for after it had already passed.
    assert completions.calls <= 4, (
        f"the bound was checked too late: {completions.calls} requests issued"
    )
    assert completions.calls < 40, "the bound must cut the act far short of its budget"


def test_the_wrap_up_request_is_kept_because_a_partial_answer_beats_none():
    """Stopping on the clock must still produce an answer.

    An act that returns nothing leaves the strategy with no patch at all, which
    scores as a total failure; a partial answer at least reflects what the model
    had established. This is the same reasoning as the turn and cost guards.
    """
    completions = _SlowCompletions(per_call_seconds=0.05, calls_before_final=99)
    client = _FakeClient(completions)

    result = _run(client, max_tool_turns=40, max_wall_seconds=0.12)

    assert completions.calls >= 1
    assert result.truncated is True
    # The final-answer request is made without tools, so the model cannot loop.
    assert isinstance(result.response, str)


def test_no_bound_keeps_the_previous_unbounded_behaviour():
    """Passing 0/None must preserve historical runs exactly."""
    completions = _SlowCompletions(per_call_seconds=0.0, calls_before_final=3)
    client = _FakeClient(completions)

    result = _run(client, max_tool_turns=10, max_wall_seconds=None)

    assert result.truncated is False, "a run that finished on its own is not truncated"
    assert result.response == "done"


def test_config_supplies_the_default_bound(monkeypatch):
    """All three providers inherit the bound from Config, not from each call site.

    Threading a new parameter through three providers invites one of them being
    forgotten, and the forgotten one would be the one that hangs.
    """
    completions = _SlowCompletions(per_call_seconds=0.05)
    client = _FakeClient(completions)

    monkeypatch.setattr(tool_loop.Config, "ACT_TIMEOUT_SECONDS", 1)  # 0.05 s per call
    result = _run(client, max_tool_turns=40)  # no explicit bound
    assert completions.calls < 40, "Config.ACT_TIMEOUT_SECONDS was ignored"
    assert result.truncated is True


def test_zero_in_config_means_unbounded(monkeypatch):
    """0 must disable the guard so a historical configuration stays reproducible."""
    completions = _SlowCompletions(per_call_seconds=0.0, calls_before_final=3)
    client = _FakeClient(completions)

    monkeypatch.setattr(tool_loop.Config, "ACT_TIMEOUT_SECONDS", 0)
    result = _run(client, max_tool_turns=10)
    assert result.response == "done"
    assert result.truncated is False
