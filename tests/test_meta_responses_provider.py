"""Behaviour pins for the Meta (Responses API) provider and its tool loop.

Why these exist, in one line each:

1. RESPONSE SHAPE -- a response whose output is [reasoning, message,
   function_call] must cause the tool to run and its result to go back as a
   ``function_call_output`` item carrying the SAME ``call_id``. That pairing is
   what makes the second request legal; a dropped or mismatched call_id would
   make the model re-call the same tool forever.
2. THE SILENT-FAILURE PIN -- an empty response (no text, no tool call) or a
   response whose status is ``in_progress`` must be treated as FAILED (retried
   in place, then raised), never returned as an empty answer. Measured against
   9router (2026-10-09): the muse-spark route answers chat.completions with
   HTTP 200 + finish_reason="in_progress" + empty content + 0 tokens. Accepting
   that shape would have produced 150 empty sweep runs with no error anywhere.
3. BUDGET -- max_tool_turns and max_cost_usd must actually STOP the loop (and
   the stopped act asks for a final answer without tools, marked truncated),
   with the same semantics as the Chat Completions loop.
4. PRICING -- the contributor-tier card for oc/muse-spark-1.3-contributor-free
   must resolve: input 0.10 / cached 0.002 / output 0.20, via PricingTable.get
   and the PRICING_MODEL_OVERRIDE path.
5. LIVE -- one real call, skipped (never failed) when the endpoint is down.

No network is touched except by the single live test at the bottom.
"""

import copy
import socket
import sys
import time
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

#: The real ``time.sleep``, captured BEFORE any fixture can patch it.
#:
#: The autouse ``_no_sleep`` fixture stubs ``time.sleep`` (retry's ``time`` is
#: the shared ``time`` module, so the stub is global). That is right for the
#: retry tests and WRONG for the wall-clock tests: the wall guard reads the
#: actual clock, so a no-op sleep makes the bound unreachable and the test
#: would pass while proving nothing. Those tests restore this reference.
_REAL_SLEEP = time.sleep

from config import Config  # noqa: E402
from evaluation.cost import PricingTable  # noqa: E402
from providers import response_utils as ru  # noqa: E402
from providers import tool_loop_responses as tlr  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes that mirror the Responses API object graph
# ---------------------------------------------------------------------------

class _SummaryPart:
    def __init__(self, text):
        self.text = text
        self.type = "summary_text"


class _OutputText:
    def __init__(self, text):
        self.text = text
        self.type = "output_text"


class _FakeReasoningItem:
    def __init__(self, text="thinking..."):
        self.type = "reasoning"
        self.summary = [_SummaryPart(text)]
        self.content = None


class _FakeMessageItem:
    def __init__(self, text="", role="assistant"):
        self.type = "message"
        self.role = role
        self.content = [_OutputText(text)]


class _FakeFunctionCallItem:
    def __init__(self, name="read_file", arguments="{}", call_id="call-1"):
        self.type = "function_call"
        self.name = name
        self.arguments = arguments
        self.call_id = call_id


class _FakeUsage:
    def __init__(self, input_tokens=10, output_tokens=5, cached=None):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = input_tokens + output_tokens
        self.input_tokens_details = (
            {"cached_tokens": cached} if cached else None
        )
        # Deliberately NOT set: prompt_tokens is the Chat Completions spelling
        # and must not be what this dialect is read through.
        self.prompt_tokens_details = None


class _FakeIncompleteDetails:
    def __init__(self, reason):
        self.reason = reason


class _FakeResponse:
    def __init__(self, output=(), status="completed", usage=None,
                 incomplete_details=None):
        self.status = status
        self.output = list(output)
        self.usage = usage or _FakeUsage()
        self.incomplete_details = incomplete_details

    @property
    def output_text(self):
        """Mirror the SDK's convenience field: message text, joined."""
        parts = []
        for item in self.output:
            if getattr(item, "type", "") == "message":
                for part in item.content:
                    text = getattr(part, "text", None)
                    if isinstance(text, str):
                        parts.append(text)
        return "".join(parts)


def _text_response(text, reasoning="done thinking", usage=None):
    return _FakeResponse(
        output=[_FakeReasoningItem(reasoning), _FakeMessageItem(text)],
        usage=usage,
    )


def _tool_call_response(name="read_file", arguments='{"path": "a.py"}',
                        call_id="call-1", text="I will read it",
                        reasoning="thinking", usage=None):
    return _FakeResponse(
        output=[
            _FakeReasoningItem(reasoning),
            _FakeMessageItem(text),
            _FakeFunctionCallItem(name, arguments, call_id),
        ],
        usage=usage,
    )


def _empty_response(status="completed"):
    """The silent-failure shape: 200-ish, nothing inside."""
    return _FakeResponse(output=[], status=status)


def _in_progress_response(text="not finished"):
    return _FakeResponse(
        output=[_FakeReasoningItem(), _FakeMessageItem(text)],
        status="in_progress",
    )


class _ScriptedResponses:
    """Returns a scripted response per call; ``Exception`` entries are raised.

    ``calls`` counts HTTP attempts, not turns, so a script can express "the
    first attempt at turn 2 returns an empty body, the retry succeeds". An
    exhausted script raises, which is what pins the loop's request count.
    """

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0
        self.kwargs_seen: list[dict] = []

    def create(self, **kwargs):
        self.calls += 1
        # DEEP copy: the loop mutates its ``input`` list in place as the
        # conversation grows, so recording the reference would make every
        # snapshot show the FINAL state and hide what each request actually
        # sent (measured: the first request appeared to contain turn 1's
        # function_call).
        self.kwargs_seen.append(copy.deepcopy(kwargs))
        if not self._script:
            raise AssertionError(
                "fake client exhausted: the loop made more requests than the "
                "script provides (a retry or budget bug would show up here)"
            )
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeClient:
    """Only ``responses.create`` exists -- no chat.completions at all.

    That absence is deliberate: if the loop ever fell back to the Chat
    Completions dialect, these tests would fail with AttributeError instead of
    silently exercising the wrong endpoint.
    """

    def __init__(self, script):
        self.responses = _ScriptedResponses(script)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Retries must not actually wait in the test suite."""
    import evaluation.retry as retry_mod

    monkeypatch.setattr(retry_mod.time, "sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def _fast_tool(monkeypatch):
    """Execute no real tool; return a marker the tests can look for."""
    monkeypatch.setattr(tlr, "execute_tool", lambda name, args, role="": "TOOL_OUT")


@pytest.fixture(autouse=True)
def _test_config(monkeypatch):
    """Neutral, fast config for every test in this file.

    Patched on BOTH module references (tool_loop_responses and evaluation.retry)
    because another test module reloads config and the two can end up pointing
    at different class objects -- the same trap documented in
    tests/test_tool_loop_retry.py.
    """
    import evaluation.retry as retry_mod

    for cfg in (tlr.Config, retry_mod.Config):
        monkeypatch.setattr(cfg, "MAX_RETRIES", 3)
        monkeypatch.setattr(cfg, "RATE_LIMIT_BACKOFF_BASE", 0.0)
        monkeypatch.setattr(cfg, "RATE_LIMIT_BACKOFF_MAX", 0.0)
        monkeypatch.setattr(cfg, "TOOL_OUTPUT_MAX_CHARS", 2000)
        monkeypatch.setattr(cfg, "ACT_TIMEOUT_SECONDS", 0)   # unbounded
        monkeypatch.setattr(cfg, "STALL_TIMEOUT_SECONDS", 0)  # guard off


def _run(client, *, max_tool_turns=5, role="direct", **kwargs):
    return tlr.run_tool_loop_responses(
        client,
        model="fake-model",
        prompt="fix it",
        role=role,
        # Nested Chat-Completions schema on purpose: the loop must flatten it.
        tools=[{"type": "function",
                "function": {"name": "read_file", "parameters": {}}}],
        max_tool_turns=max_tool_turns,
        repo_root=None,
        **kwargs,
    )


def _function_calls_in(kwargs: dict) -> list[dict]:
    return [i for i in kwargs["input"] if i.get("type") == "function_call"]


def _function_outputs_in(kwargs: dict) -> list[dict]:
    return [i for i in kwargs["input"] if i.get("type") == "function_call_output"]


# ---------------------------------------------------------------------------
# 1. Response shape: reasoning + message + function_call -> execute + reply
# ---------------------------------------------------------------------------

def test_reasoning_message_function_call_executes_tool_and_replies(monkeypatch):
    """The [reasoning, message, function_call] shape must run the tool and send
    a function_call_output carrying the SAME call_id."""
    executed = []

    def _fake_execute(name, args, role=""):
        executed.append((name, args))
        return "FILE CONTENTS"

    monkeypatch.setattr(tlr, "execute_tool", _fake_execute)
    client = _FakeClient([
        _tool_call_response("read_file", '{"path": "setup.py"}', "call_abc"),
        _text_response("setup.py read: done"),
    ])

    result = _run(client)

    assert result.response == "setup.py read: done"
    assert executed == [("read_file", {"path": "setup.py"})], "tool must run locally"
    assert result.tool_calls == [
        {"name": "read_file", "arguments": {"path": "setup.py"},
         "result": "FILE CONTENTS"}
    ], "the call must be recorded for the artifact trail"

    first, second = client.responses.kwargs_seen
    assert first["input"] == [{"role": "user", "content": "fix it"}]
    assert "instructions" in first, "the system prompt travels as instructions"

    sent_calls = _function_calls_in(second)
    sent_outputs = _function_outputs_in(second)
    assert [c["call_id"] for c in sent_calls] == ["call_abc"]
    assert sent_calls[0]["name"] == "read_file"
    assert sent_calls[0]["arguments"] == '{"path": "setup.py"}'
    assert [o["call_id"] for o in sent_outputs] == ["call_abc"], (
        "the output must carry the function call's call_id, or the model will "
        "repeat the same call forever"
    )
    assert sent_outputs[0]["output"] == "FILE CONTENTS"


def test_tools_are_sent_in_the_flat_responses_form(monkeypatch):
    """Nested Chat-Completions schemas must be flattened for this endpoint."""
    client = _FakeClient([_text_response("done")])
    _run(client)

    tools = client.responses.kwargs_seen[0]["tools"]
    assert tools, "the loop must send tools when it has them"
    for tool in tools:
        assert "function" not in tool, (
            "the Responses API rejects the nested Chat-Completions form; "
            f"got {tool}"
        )
        assert tool["type"] == "function" and tool["name"] == "read_file"


def test_reasoning_and_tool_results_land_in_the_trajectory(monkeypatch):
    """The artifact must carry per-turn reasoning and the full tool result."""
    monkeypatch.setattr(tlr, "execute_tool", lambda n, a, role="": "FILE CONTENTS")
    client = _FakeClient([
        _tool_call_response("read_file", '{"path": "a.py"}', "call_abc",
                            reasoning="I need to see a.py"),
        _text_response("done", reasoning="the file answers it"),
    ])

    result = _run(client)

    assistants = [e for e in result.trajectory if e["type"] == "assistant"]
    assert assistants[0]["reasoning"] == "I need to see a.py"
    assert assistants[0]["tool_calls"] == [
        {"id": "call_abc", "name": "read_file", "arguments": '{"path": "a.py"}'}
    ]
    tools = [e for e in result.trajectory if e["type"] == "tool"]
    assert tools[0]["tool_call_id"] == "call_abc"
    assert tools[0]["name"] == "read_file"
    assert tools[0]["arguments"] == {"path": "a.py"}
    assert tools[0]["result"] == "FILE CONTENTS"


def test_large_tool_output_is_truncated_keeping_head_and_tail(monkeypatch):
    """Late turns re-send the whole conversation, so results must be capped."""
    big = "HEAD" + ("x" * 5000) + "TAIL"
    monkeypatch.setattr(tlr, "execute_tool", lambda n, a, role="": big)
    monkeypatch.setattr(tlr.Config, "TOOL_OUTPUT_MAX_CHARS", 200)

    client = _FakeClient([_tool_call_response(call_id="c1"), _text_response("done")])
    result = _run(client)

    sent = _function_outputs_in(client.responses.kwargs_seen[1])
    assert len(sent[0]["output"]) < len(big), "the output should have been capped"
    assert sent[0]["output"].startswith("HEAD"), "head must be kept"
    assert sent[0]["output"].endswith("TAIL"), "tail must be kept"
    assert "chars omitted" in sent[0]["output"]

    # The artifact preview is capped at 2000 chars; the trajectory keeps the
    # FULL result, because the line that explains a decision is often the one
    # a preview cuts.
    assert len(result.tool_calls[0]["result"]) <= 2000
    tool_entry = [e for e in result.trajectory if e["type"] == "tool"][0]
    assert tool_entry["result"] == big
    assert tool_entry["result_chars"] == len(big)


def test_repo_root_and_test_guard_are_synced_for_every_act(monkeypatch):
    """The loop must use the shared sandbox sync, in the same order as the
    Chat Completions loop: set_repo_root first, then reset_test_guard."""
    seen = []
    monkeypatch.setattr(tlr, "set_repo_root", lambda p: seen.append(("root", p)))
    monkeypatch.setattr(tlr, "reset_test_guard", lambda: seen.append(("reset", None)))

    client = _FakeClient([_text_response("done")])
    tlr.run_tool_loop_responses(
        client, model="fake-model", prompt="fix", role="direct",
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
        max_tool_turns=2, repo_root="C:/repo",
    )

    assert seen == [("root", "C:/repo"), ("reset", None)]


# ---------------------------------------------------------------------------
# 2. THE SILENT-FAILURE PIN
# ---------------------------------------------------------------------------

def test_empty_response_is_retried_in_place_not_returned(monkeypatch):
    """A no-text/no-tool-call response is a dead end; resend that one request."""
    client = _FakeClient([_empty_response(), _text_response("done")])

    result = _run(client)

    assert result.response == "done"
    assert client.responses.calls == 2, "the empty attempt must be retried once"


def test_in_progress_status_is_a_failure_even_with_text(monkeypatch):
    """status="in_progress" means the server did not finish -- never an answer."""
    client = _FakeClient([_in_progress_response("partial text"), _text_response("done")])

    result = _run(client)

    assert result.response == "done", (
        "an in_progress response must be retried, not accepted"
    )
    assert client.responses.calls == 2


def test_persistently_empty_response_raises_instead_of_returning_empty(monkeypatch):
    """After the retries, an empty answer must RAISE, not become a result.

    This is the exact bug that would have made the sweep fail silently: the
    retry helper returns (not raises) the last retryable result, so the loop
    itself must refuse it.
    """
    client = _FakeClient([_empty_response()] * 5)

    with pytest.raises(tlr.EmptyResponseError):
        _run(client)

    assert client.responses.calls == 3, (
        "one turn x 3 attempts, then it must give up; got "
        f"{client.responses.calls} requests"
    )


def test_tool_call_with_empty_text_is_not_retried(monkeypatch):
    """Empty text WITH a tool call is normal tool calling -- do not resend."""
    client = _FakeClient([
        _tool_call_response(call_id="c1", text=""),
        _text_response("done"),
    ])

    result = _run(client)

    assert result.response == "done"
    assert client.responses.calls == 2, "no retry should have happened"


# ---------------------------------------------------------------------------
# 3. Budgets actually bind
# ---------------------------------------------------------------------------

def test_max_tool_turns_stops_the_loop_and_asks_for_a_final_answer(monkeypatch):
    client = _FakeClient([
        _tool_call_response(call_id="c1"),
        _tool_call_response(call_id="c2"),
        _tool_call_response(call_id="c3"),
        _text_response("final answer"),
    ])

    result = _run(client, max_tool_turns=3)

    # 3 budgeted tool turns + 1 wrap-up final-answer request. The wrap-up IS a
    # logical turn, exactly as in the Chat Completions loop -- what must not
    # grow is the number of TOOL turns, which is capped at 3.
    assert result.api_turns == 4
    assert client.responses.calls == 4, "3 turns + 1 wrap-up request"
    assert result.truncated is True, "a bounded act is not a clean finish"
    assert result.response == "final answer"
    assert len(result.tool_calls) == 3

    bounds = [e for e in result.trajectory if e["type"] == "bound_reached"]
    assert bounds and bounds[0]["stop_reason"] == "max_tool_turns"
    assert bounds[0]["granted_turns"] == 3

    # The wrap-up request goes out WITHOUT tools so the model cannot loop.
    assert "tools" not in client.responses.kwargs_seen[-1]


def test_max_cost_usd_stops_the_loop(monkeypatch):
    """The guard stops the loop before the request that would exceed the cap."""
    # Price the guard so 100k input tokens cost $0.10 per request.
    monkeypatch.setattr(
        tlr.PricingTable,
        "rates_for",
        lambda model, window="off_peak": {
            "input_per_million": 1.0,
            "cached_input_per_million": 0.0,
            "output_per_million": 1.0,
        },
    )
    client = _FakeClient([
        _tool_call_response(call_id="c1",
                            usage=_FakeUsage(input_tokens=100_000, output_tokens=0)),
        _text_response("final answer"),
    ])

    result = _run(client, max_tool_turns=10, max_cost_usd=0.05)

    assert result.truncated is True
    assert client.responses.calls == 2, (
        "turn 1 (paid, $0.10) then the wrap-up; turn 2 must never be issued"
    )
    bounds = [e for e in result.trajectory if e["type"] == "bound_reached"]
    assert bounds and bounds[0]["stop_reason"] == "cost"


def test_an_exhausted_cost_cap_still_arms_the_guard(monkeypatch):
    """max_cost_usd=0.0 means "budget spent", not "no budget".

    Testing truthiness instead of `is not None` would read the spent budget as
    no budget and keep spending precisely when it should stop.
    """
    client = _FakeClient([_text_response("final answer")])

    result = _run(client, max_tool_turns=10, max_cost_usd=0.0)

    assert client.responses.calls == 1, "no turn may be issued under a spent cap"
    bounds = [e for e in result.trajectory if e["type"] == "bound_reached"]
    assert bounds and bounds[0]["stop_reason"] == "cost"
    assert result.truncated is True


# ---------------------------------------------------------------------------
# 3b. The STALL path and the WALL-CLOCK bound (mutation survivors R2 / B4)
# ---------------------------------------------------------------------------
# Two mutations of the Responses loop survived the original suite:
#   R2: dropping StalledError from fatal_on  -> a wedged connection would be
#       retried, multiplying one 900s bound into 3 x 900s.
#   B4: disabling the wall guard             -> an act could run past its
#       wall-clock bound.
# STALL_TIMEOUT_SECONDS=900 is ACTIVE in the sweep (only ACT_TIMEOUT_SECONDS is
# pinned to 0), and the stall path exists because of the EXP-20261007-003 hang
# (3 hours, CPU 0, no log line). These tests mirror the Chat Completions stall
# tests in test_docker_gate_and_stall_watchdog.py.

def test_a_stall_is_fatal_and_is_not_retried(monkeypatch):
    """ONE stall must cost ONE bound, not three.

    The loop retries a failed request up to Config.MAX_RETRIES times. If a
    stall were retried like an ordinary error, one wedged connection would cost
    3 x 900s = 45 minutes of a 150-run sweep, all blocking on the same wedged
    endpoint. The assertion is the ATTEMPT COUNT: exactly 1.
    """
    import evaluation.retry as retry_mod
    from providers.tool_loop import StalledError

    calls = {"n": 0}

    class _StallingResponses:
        def create(self, **kwargs):
            calls["n"] += 1
            raise StalledError("no response within 900s")

    client = _FakeClient.__new__(_FakeClient)
    client.responses = _StallingResponses()

    monkeypatch.setattr(retry_mod.time, "sleep", lambda _s: None)

    with pytest.raises(StalledError):
        _run(client)

    assert calls["n"] == 1, (
        f"the stalled request was attempted {calls['n']} time(s); a stall is "
        f"fatal -- retrying it multiplies one 900s bound into "
        f"{calls['n']} x 900s on the same wedged connection"
    )


def test_an_ordinary_error_is_still_retried(monkeypatch):
    """The other half: fatal_on must not make EVERY error fatal.

    Without this, marking all exceptions fatal would satisfy the stall test
    while removing the retry behaviour the loop depends on for transient
    provider blips.
    """
    import evaluation.retry as retry_mod

    calls = {"n": 0}

    class _FlakyResponses:
        def create(self, **kwargs):
            calls["n"] += 1
            raise ConnectionError("connection reset by peer")

    client = _FakeClient.__new__(_FakeClient)
    client.responses = _FlakyResponses()

    monkeypatch.setattr(retry_mod.time, "sleep", lambda _s: None)

    with pytest.raises(ConnectionError):
        _run(client)

    assert calls["n"] > 1, (
        f"a transient provider error was attempted only {calls['n']} time(s); "
        f"the retry path must still work for ordinary failures"
    )


def test_the_wall_clock_bound_actually_stops_the_act(monkeypatch):
    """The wall guard must END the act, not merely be recorded.

    Every request burns a real fraction of a second. The autouse ``_no_sleep``
    fixture is UNDONE here (``_REAL_SLEEP``): the guard reads the actual clock,
    so a stubbed sleep would make the bound unreachable and this test would
    pass while proving nothing. With the bound set well below what the full
    turn budget would take, the loop must stop early and report truncation.
    """
    monkeypatch.setattr(time, "sleep", _REAL_SLEEP)

    class _SlowResponses:
        def __init__(self):
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            time.sleep(0.05)
            return _tool_call_response(call_id=f"c{self.calls}")

    client = _FakeClient.__new__(_FakeClient)
    client.responses = _SlowResponses()

    result = _run(client, max_tool_turns=40, max_wall_seconds=0.12)

    assert client.responses.calls < 40, (
        f"the loop ran all {client.responses.calls} turns despite a 0.12s bound"
    )
    assert result.truncated is True, (
        "an act stopped by the clock was cut off by a bound, exactly like one "
        "stopped by turns or cost; it must not be reported as a clean finish"
    )
    bounds = [e for e in result.trajectory if e["type"] == "bound_reached"]
    assert bounds and bounds[0]["stop_reason"] == "wall_clock"


def test_zero_act_timeout_means_unbounded(monkeypatch):
    """ACT_TIMEOUT_SECONDS=0 must resolve to NO wall bound, per the sweep pin.

    The sweep pins ACT_TIMEOUT_SECONDS=0, so this is the configuration the
    production runs actually use: a slow-but-healthy act must be allowed to
    finish, and a zero bound must NOT be read as "stop immediately".
    """
    monkeypatch.setattr(time, "sleep", _REAL_SLEEP)

    class _SlowResponses:
        def __init__(self):
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            time.sleep(0.02)
            if self.calls >= 3:
                return _text_response("done")
            return _tool_call_response(call_id=f"c{self.calls}")

    client = _FakeClient.__new__(_FakeClient)
    client.responses = _SlowResponses()

    # No explicit max_wall_seconds: the loop reads Config.ACT_TIMEOUT_SECONDS
    # (patched to 0 by the _test_config fixture).
    result = _run(client, max_tool_turns=10)

    assert result.truncated is False, (
        "ACT_TIMEOUT_SECONDS=0 was read as a real bound; the unbounded path is "
        "broken (the sweep runs with this value)"
    )
    assert result.response == "done"


def test_config_supplies_the_default_bound(monkeypatch):
    """All providers inherit the bound from Config, not from each call site."""
    monkeypatch.setattr(time, "sleep", _REAL_SLEEP)

    class _SlowResponses:
        def __init__(self):
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            time.sleep(0.05)
            return _tool_call_response(call_id=f"c{self.calls}")

    client = _FakeClient.__new__(_FakeClient)
    client.responses = _SlowResponses()

    monkeypatch.setattr(tlr.Config, "ACT_TIMEOUT_SECONDS", 1)
    result = _run(client, max_tool_turns=40)  # no explicit bound

    assert client.responses.calls < 40, "Config.ACT_TIMEOUT_SECONDS was ignored"
    assert result.truncated is True


def test_readonly_role_nudge_never_says_edit_file(monkeypatch):
    """The wrap-up nudge is shared with the Chat Completions loop, so a
    read-only role must never be told to edit (it has no edit_file tool)."""
    client = _FakeClient([_tool_call_response(call_id="c1"), _text_response("verdict")])
    _run(client, role="reviewer", max_tool_turns=2)

    nudges = [
        item["content"]
        for kwargs in client.responses.kwargs_seen
        for item in kwargs["input"]
        if item.get("role") == "user"
        and isinstance(item.get("content"), str)
        and "almost out of tool budget" in item["content"]
    ]
    assert nudges, "the loop should have sent a wrap-up nudge"
    for n in nudges:
        assert "edit_file" not in n
        assert "final answer" in n


# ---------------------------------------------------------------------------
# InferenceResult completeness
# ---------------------------------------------------------------------------

def test_inference_result_is_fully_populated(monkeypatch):
    client = _FakeClient([
        _tool_call_response(call_id="c1", usage=_FakeUsage(100, 20)),
        _text_response("done", usage=_FakeUsage(150, 30)),
    ])

    result = _run(client)

    assert result.model == "fake-model"
    assert result.finish_reason == "stop"
    assert result.response == "done"
    assert result.api_turns == 2
    assert result.execution_time > 0
    assert result.usage["prompt_tokens"] == 250
    assert result.usage["completion_tokens"] == 50
    assert result.usage["total_tokens"] == 300
    assert result.truncated is False
    assert result.tool_calls and result.trajectory


# ---------------------------------------------------------------------------
# 4. Pricing
# ---------------------------------------------------------------------------

def test_muse_spark_card_is_looked_up_with_the_contributor_rates(monkeypatch):
    """PricingTable.get must resolve the card added in commit 8446eb1."""
    monkeypatch.setattr(Config, "PRICING_MODEL_OVERRIDE", "")
    model = "oc/muse-spark-1.3-contributor-free"

    card = PricingTable.get(model)
    assert card is not None, "the contributor-tier card is missing"
    assert card["input_per_million"] == 0.10
    assert card["cached_input_per_million"] == 0.002
    assert card["output_per_million"] == 0.20

    # Flat card: every window resolves to the same rates.
    for window in ("off_peak", "peak"):
        rates = PricingTable.rates_for(model, window)
        assert (
            rates["input_per_million"],
            rates["cached_input_per_million"],
            rates["output_per_million"],
        ) == (0.10, 0.002, 0.20), f"{window} resolved to the wrong card"


def test_pricing_model_override_path_resolves_the_card(monkeypatch):
    """When the sweep pins PRICING_MODEL_OVERRIDE to the muse card, a lookup
    for ANY model resolves to it -- that is the reported-price path."""
    monkeypatch.setattr(Config, "PRICING_MODEL_OVERRIDE",
                        "oc/muse-spark-1.3-contributor-free")

    card = PricingTable.get("some-other-model")
    assert card is not None, "the override path must resolve the muse card"
    assert card["input_per_million"] == 0.10
    assert card["output_per_million"] == 0.20


# ---------------------------------------------------------------------------
# Response-shape helpers (unit level)
# ---------------------------------------------------------------------------

class TestResponsesShapeHelpers:
    def test_flatten_tool_schemas_converts_nested_and_passes_flat_through(self):
        nested = [{"type": "function",
                   "function": {"name": "read_file", "description": "d",
                                "parameters": {"type": "object"}}}]
        flat = ru.flatten_tool_schemas(nested)
        assert flat == [{"type": "function", "name": "read_file",
                         "description": "d", "parameters": {"type": "object"}}]
        assert ru.flatten_tool_schemas(flat) == flat, "already-flat must pass through"

    def test_output_text_falls_back_to_walking_message_items(self):
        """A gateway variant without output_text must still yield the answer."""
        class _NoConvenienceField:
            output = [_FakeMessageItem("hello "), _FakeMessageItem("world")]

        assert ru.extract_responses_text(_NoConvenienceField()) == "hello world"

    def test_reasoning_is_read_from_summary_parts(self):
        response = _FakeResponse(output=[_FakeReasoningItem("why I did it")])
        assert ru.extract_responses_reasoning(response) == "why I did it"

    def test_finish_reason_maps_onto_the_chat_vocabulary(self):
        assert ru.responses_finish_reason(_FakeResponse(status="completed")) == "stop"
        assert ru.responses_finish_reason(_FakeResponse(status="in_progress")) == "in_progress"
        assert ru.responses_finish_reason(_FakeResponse(status="incomplete")) == "incomplete"
        assert ru.responses_finish_reason(_FakeResponse(
            status="incomplete",
            incomplete_details=_FakeIncompleteDetails("max_output_tokens"),
        )) == "length", (
            "an output-ceiling cutoff must read as 'length'; downstream "
            "extract_diff/review branch on it"
        )

    def test_empty_detector(self):
        assert ru.responses_response_is_empty(_empty_response()) is True
        assert ru.responses_response_is_empty(_text_response("hi")) is False
        assert ru.responses_response_is_empty(_tool_call_response()) is False, (
            "a tool call with empty text is normal tool calling, not empty"
        )

    def test_retryable_contract(self):
        # Dialect-named on purpose: the Chat Completions predicate is
        # ``tool_loop._retryable_chat_response``; two same-named functions with
        # different semantics was the maintenance hazard this rename removes.
        assert tlr._retryable_responses_response(_empty_response()) is True
        assert tlr._retryable_responses_response(_in_progress_response("partial")) is True
        assert tlr._retryable_responses_response(_text_response("done")) is False
        assert tlr._retryable_responses_response(_tool_call_response(text="")) is False

    def test_empty_response_error_is_shared_with_the_chat_loop(self):
        """The two loops must raise the SAME class, from ONE definition.

        The Responses loop re-exports it for backwards-compatible imports; the
        identity check is what proves there is no second definition that could
        drift (e.g. one being caught by the runner's ERROR path and the other
        not).
        """
        from providers import tool_loop as tl_mod

        assert tlr.EmptyResponseError is tl_mod.EmptyResponseError

    def test_cached_tokens_are_read_from_the_responses_dialect(self):
        usage = _FakeUsage(input_tokens=1000, output_tokens=10, cached=700)
        response = _FakeResponse(output=[_FakeMessageItem("hi")], usage=usage)

        result = ru.build_responses_inference_result(
            response, role="x", model="m", elapsed=0.1
        )

        assert result.usage["prompt_tokens"] == 1000
        assert result.usage["cached_tokens"] == 700, (
            "cached tokens must survive the input_tokens_details spelling"
        )


# ---------------------------------------------------------------------------
# Provider-level behaviour
# ---------------------------------------------------------------------------

class _ExplodingModels:
    """A listing that fails loudly if anyone consults it."""

    def list(self):  # pragma: no cover - must never be reached
        raise AssertionError(
            "health_check must not consult GET /v1/models: the muse-spark "
            "model is NOT listed there, yet calls to it succeed"
        )


needs_key = pytest.mark.skipif(
    not Config.OPENCODE_API_KEY,
    reason="requires OPENCODE_API_KEY (the 9router proxy credential)",
)


@needs_key
def test_health_check_does_not_consult_the_model_listing():
    from providers.meta_provider import MetaProvider

    provider = MetaProvider()
    client = _FakeClient([_text_response("OK")])
    client.models = _ExplodingModels()  # any listing call would raise
    provider.client = client

    assert provider.health_check() is True


@needs_key
def test_health_check_fails_on_an_empty_answer():
    from providers.meta_provider import MetaProvider

    provider = MetaProvider()
    provider.client = _FakeClient([_empty_response()])

    assert provider.health_check() is False


@needs_key
def test_health_check_fails_when_the_endpoint_errors():
    from providers.meta_provider import MetaProvider

    provider = MetaProvider()
    provider.client = _FakeClient([ConnectionError("connection refused")])

    assert provider.health_check() is False


@needs_key
def test_generate_returns_text_and_requests_json_output():
    from providers.meta_provider import MetaProvider

    provider = MetaProvider()
    client = _FakeClient([_text_response('{"ok": true}')])
    provider.client = client

    result = provider.generate('Reply with JSON: {"ok": true}', role="direct")

    assert result.response == '{"ok": true}'
    kwargs = client.responses.kwargs_seen[0]
    assert kwargs["text"] == {"format": {"type": "json_object"}}
    assert "tools" not in kwargs, "generate() is the single-shot path"


@needs_key
def test_generate_raises_on_a_persistently_empty_response():
    """The silent-failure pin also covers the single-shot path."""
    from providers.meta_provider import MetaProvider

    provider = MetaProvider()
    provider.client = _FakeClient([_empty_response()] * 10)

    with pytest.raises(tlr.EmptyResponseError):
        provider.generate("Reply in JSON", role="direct")


@needs_key
def test_provider_with_tools_delegates_to_the_responses_loop():
    """generate_with_tools must run the Responses loop and return its result."""
    from providers.meta_provider import MetaProvider

    provider = MetaProvider()
    client = _FakeClient([
        _tool_call_response("read_file", '{"path": "a.py"}', "call_zzz"),
        _text_response("done via tools"),
    ])
    provider.client = client

    result = provider.generate_with_tools(
        "fix it", role="executor",
        tools=[{"type": "function",
                "function": {"name": "read_file", "parameters": {}}}],
        max_tool_turns=3, repo_root=None,
    )

    assert result.response == "done via tools"
    assert result.tool_calls[0]["name"] == "read_file"
    sent = _function_outputs_in(client.responses.kwargs_seen[1])
    assert sent[0]["call_id"] == "call_zzz"


# ---------------------------------------------------------------------------
# 5. LIVE test -- ONE real call, skipped (never failed) when the endpoint is
#    unavailable. The model is not in GET /v1/models, so reachability is a TCP
#    check on the proxy plus the presence of a key; the call itself is the
#    availability proof.
# ---------------------------------------------------------------------------

def _endpoint_reachable() -> bool:
    parsed = urllib.parse.urlparse(Config.OPENCODE_BASE_URL)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


requires_responses_endpoint = pytest.mark.skipif(
    not Config.OPENCODE_API_KEY or not _endpoint_reachable(),
    reason="requires the 9router proxy (OPENCODE_BASE_URL) and OPENCODE_API_KEY",
)


@requires_responses_endpoint
def test_live_single_call_returns_text():
    """ONE real call through the provider. Skips when the endpoint is down."""
    import openai

    from providers.meta_provider import MetaProvider

    provider = MetaProvider()
    try:
        result = provider.generate('Reply with JSON: {"pong": true}',
                                   role="live-check")
    except (openai.APIConnectionError, openai.APITimeoutError) as exc:
        pytest.skip(f"9router endpoint unavailable: {exc}")

    assert result.response.strip(), "the model answered with no text"
