"""The run must be recorded as a TRAJECTORY, not a summary of its last message.

What was missing, and why it mattered
-------------------------------------

Before this, the only trace of a tool-calling act was:

* ``<role>.md`` -- the act's FINAL response text. Per-role filenames mean a later
  act OVERWRITES an earlier one, so on a review run (two executor acts) the first
  executor attempt was simply gone from the artifacts.
* ``tool_calls.jsonl`` -- a flat list of calls with a 2000-char result preview.
  No turn number, so it cannot say which turn produced which call, and no
  reasoning at all.

So for the pilot run EXP-20260930-215 the question "why did the revision act make
0 edits?" could only be answered by reading the raw experiment log. The artifacts
could not answer it, even though they were the record of the run.

What these tests pin
--------------------

1. Every assistant turn is recorded, INCLUDING the one that ends the act (a record
   that only keeps tool-calling turns looks like the act stopped mid-flight).
2. The reasoning channel is captured per turn, not only on the final answer.
3. Each tool result is recorded in full, paired with the call that produced it.
4. A bound-reached act says so INSIDE the trajectory. A reader must be able to
   tell "finished on its own" from "was cut off" without grepping a log -- the
   distinction EXP-20260928-003's 8/8/6 headline collapsed.
5. The record survives onto the blackboard message (``AgentMessage.trajectory``),
   which is what gets serialised to ``messages.jsonl``; an InferenceResult is
   consumed and never written, so anything not copied there is lost.
6. Provider reasoning field names vary (``reasoning_content`` / ``reasoning`` /
   ``thinking``, string or list) and all of them are read.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agents.messages import AgentMessage  # noqa: E402
from providers import tool_loop  # noqa: E402
from providers.response_utils import _extract_reasoning  # noqa: E402


class _FakeFunction:
    def __init__(self, name="read_file", arguments='{"path": "a.py"}'):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, idx=0, name="read_file", arguments='{"path": "a.py"}'):
        self.id = f"call-{idx}"
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, tool_calls=None, content="", reasoning_content="", **extra):
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning_content = reasoning_content
        for key, value in extra.items():
            setattr(self, key, value)


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
    def __init__(self, msg, usage=None, finish_reason="stop"):
        self.choices = [_FakeChoice(msg, finish_reason)]
        self.usage = usage or _FakeUsage()


class _ScriptedCompletions:
    """Returns a fixed sequence of responses, then repeats the last one."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.kwargs_seen = []

    def create(self, **kwargs):
        self.kwargs_seen.append(kwargs)
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[idx]


class _FakeClient:
    def __init__(self, completions):
        self.chat = type("Chat", (), {})()
        self.chat.completions = completions


def _run(client, **kwargs):
    kwargs.setdefault("max_tool_turns", 10)
    return tool_loop.run_tool_loop(
        client,
        model="fake",
        prompt="fix it",
        role="executor",
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
        repo_root=None,
        **kwargs,
    )


def _patch_tool(monkeypatch, output="FILE CONTENTS"):
    monkeypatch.setattr(tool_loop, "execute_tool", lambda name, args, role="": output)
    monkeypatch.setattr(tool_loop.Config, "TOOL_OUTPUT_MAX_CHARS", 2000)


# --------------------------------------------------------------- what is recorded
def test_every_assistant_turn_is_recorded_including_the_final_one(monkeypatch):
    """The turn that ENDS the act must be in the record.

    Recording only turns that call a tool would leave a trajectory whose last
    entry is a tool result -- indistinguishable from an act that was cut off
    mid-flight. The final answer is the agent's own conclusion, so it belongs.
    """
    _patch_tool(monkeypatch)
    completions = _ScriptedCompletions([
        _FakeResponse(_FakeMessage([_FakeToolCall(1)], reasoning_content="I should read a.py")),
        _FakeResponse(_FakeMessage(None, content="The fix is X", reasoning_content="Now I know")),
    ])
    result = _run(_FakeClient(completions))

    assistants = [e for e in result.trajectory if e["type"] == "assistant"]
    assert len(assistants) == 2, f"expected both assistant turns, got {len(assistants)}"
    assert assistants[0]["tool_calls"], "the tool-calling turn must list its calls"
    assert not assistants[1]["tool_calls"], "the final turn made no call"
    assert assistants[1]["content"] == "The fix is X"


def test_reasoning_is_captured_per_turn_not_only_at_the_end(monkeypatch):
    """Reasoning between tool calls is the whole point of a trajectory.

    The final answer's reasoning was already saved to ``<role>_reasoning.md``; what
    was missing is the reasoning attached to EACH intermediate turn -- the part
    that explains why the agent chose each action.
    """
    _patch_tool(monkeypatch)
    completions = _ScriptedCompletions([
        _FakeResponse(_FakeMessage([_FakeToolCall(1)], reasoning_content="step one reasoning")),
        _FakeResponse(_FakeMessage([_FakeToolCall(2)], reasoning_content="step two reasoning")),
        _FakeResponse(_FakeMessage(None, content="done", reasoning_content="final reasoning")),
    ])
    result = _run(_FakeClient(completions))

    reasoning = [e["reasoning"] for e in result.trajectory if e["type"] == "assistant"]
    assert reasoning == ["step one reasoning", "step two reasoning", "final reasoning"]


def test_each_tool_result_is_recorded_in_full_and_paired_with_its_call(monkeypatch):
    """A trajectory must show what the agent SAW, not a preview of it.

    ``tool_calls.jsonl`` keeps a 2000-char preview, which is fine for a summary but
    cuts exactly the tail where an error or a test result lives. The trajectory
    keeps the whole output and records which call produced it.
    """
    long_output = "x" * 5000
    _patch_tool(monkeypatch, output=long_output)
    completions = _ScriptedCompletions([
        _FakeResponse(_FakeMessage([_FakeToolCall(1, "read_file", '{"path": "a.py"}')])),
        _FakeResponse(_FakeMessage(None, content="done")),
    ])
    result = _run(_FakeClient(completions))

    tools = [e for e in result.trajectory if e["type"] == "tool"]
    assert len(tools) == 1
    entry = tools[0]
    assert entry["name"] == "read_file"
    assert entry["arguments"] == {"path": "a.py"}
    assert entry["result"] == long_output, "the full result must be kept, not truncated"
    assert entry["result_chars"] == 5000
    assert entry["turn"] == 1, "the result must be tied to the turn that produced it"


def test_a_bound_reached_act_says_so_inside_the_trajectory(monkeypatch):
    """A reader must see the cutoff in the record, without grepping a log.

    This is the distinction that made EXP-20260928-003's headline unreadable: an
    act that hit its bound was indistinguishable from one that finished, so a
    budget artifact could be read as a strategy outcome.
    """
    _patch_tool(monkeypatch)
    # Always asks for another tool, so the loop can only end by hitting the bound.
    completions = _ScriptedCompletions([
        _FakeResponse(_FakeMessage([_FakeToolCall(1)])),
    ])
    result = _run(_FakeClient(completions), max_tool_turns=3)

    assert result.truncated is True
    bounds = [e for e in result.trajectory if e["type"] == "bound_reached"]
    assert len(bounds) == 1, "the cutoff must be recorded in the trajectory"
    assert bounds[0]["stop_reason"] == "max_tool_turns"
    assert bounds[0]["granted_turns"] == 3

    # And the wrap-up answer is marked as such, so it is not mistaken for a
    # conclusion the agent reached on its own.
    final = [e for e in result.trajectory if e.get("is_final_answer_after_bound")]
    assert len(final) == 1
    assert final[0]["type"] == "assistant"


def test_a_cost_stop_is_labelled_by_its_cause_not_as_a_turn_limit(monkeypatch):
    """The three bounds must be distinguishable in the record.

    Reporting every cutoff as "hit max_tool_turns" would send a reader looking for
    a budget problem when the run actually died of cost.
    """
    _patch_tool(monkeypatch)
    completions = _ScriptedCompletions([_FakeResponse(_FakeMessage([_FakeToolCall(1)]))])
    result = _run(
        _FakeClient(completions),
        max_tool_turns=50,
        max_cost_usd=0.0,  # exhausted before the first request
    )

    bounds = [e for e in result.trajectory if e["type"] == "bound_reached"]
    assert bounds and bounds[0]["stop_reason"] == "cost", (
        f"expected a cost stop, got {bounds}"
    )


# --------------------------------------------------- the record reaches the message
def test_the_trajectory_reaches_the_blackboard_message():
    """What is not copied onto AgentMessage never reaches messages.jsonl.

    The InferenceResult is consumed by the strategy and never serialised, so the
    message is the only carrier of the record. This is the link that makes
    ``messages.jsonl`` a trajectory rather than a list of summaries.
    """
    msg = AgentMessage(
        sender="executor",
        receiver="orchestrator",
        content="done",
        kind="result",
        trajectory=[{"type": "assistant", "turn": 1, "content": "hi", "reasoning": "why"}],
        truncated=True,
    )
    payload = msg.to_dict()
    assert payload["trajectory"], "the trajectory must survive serialisation"
    assert payload["trajectory"][0]["reasoning"] == "why"
    assert payload["truncated"] is True

    # It must also round-trip through JSON, since that is how it is written.
    assert json.loads(json.dumps(payload))["trajectory"][0]["content"] == "hi"


# ------------------------------------------------------- the reasoning artifact
def test_reasoning_artifact_is_written_from_every_turn_not_just_the_last(monkeypatch, tmp_path):
    """Writing only the FINAL response's reasoning loses most of it.

    Measured on EXP-20260930-249 (thinking ON): 12 of 19 assistant turns carried a
    reasoning channel, but the FINAL turn carried none. The old writer looked only
    at ``inference.reasoning_content`` -- the final response -- so it produced NO
    reasoning file at all, and a reader would conclude thinking was off while
    twelve turns of it sat in the record.

    This pins the fix: the artifact is built from every turn of the trajectory.
    """
    from experiments import runner

    class _Inf:
        role = "executor"
        response = "final answer"
        reasoning_content = ""  # the final response had none, as measured
        tool_calls = []
        trajectory = [
            {"type": "assistant", "turn": 1, "reasoning": "first thought"},
            {"type": "tool", "turn": 1, "name": "read_file", "result": "x"},
            {"type": "assistant", "turn": 2, "reasoning": "second thought"},
            {"type": "assistant", "turn": 3, "reasoning": ""},  # no reasoning
        ]

    runner._save_artifacts(
        output_dir=str(tmp_path),
        instance_id="inst",
        strategy_name="review",
        inferences=[_Inf()],
        final_patch="diff",
    )

    path = tmp_path / "artifacts" / "inst" / "review" / "executor_reasoning.md"
    assert path.exists(), (
        "the reasoning file must be written from the trajectory, not only from "
        "the final response (which had none here)"
    )
    text = path.read_text(encoding="utf-8")
    assert "first thought" in text
    assert "second thought" in text
    assert "2 turn(s)" in text


def test_trajectory_artifacts_are_written_for_every_act(monkeypatch, tmp_path):
    """A review run has two executor acts; BOTH must be in the trajectory.

    ``<role>.md`` is keyed by role, so the second act overwrites the first -- which
    is why the pilot's rejected-then-revised run could not be read from the
    artifacts: the first executor attempt, the one the reviewer rejected, was gone.
    The trajectory is append-only across acts, so it keeps both.
    """
    from experiments import runner

    class _Inf:
        def __init__(self, role, text, turn_offset):
            self.role = role
            self.response = text
            self.reasoning_content = ""
            self.tool_calls = []
            self.trajectory = [
                {"type": "assistant", "turn": 1, "content": text, "reasoning": ""},
            ]

    runner._save_artifacts(
        output_dir=str(tmp_path),
        instance_id="inst",
        strategy_name="review",
        inferences=[
            _Inf("executor", "FIRST attempt", 0),
            _Inf("reviewer", "rejected it", 0),
            _Inf("executor", "SECOND attempt", 0),
        ],
        final_patch="diff",
    )

    art = tmp_path / "artifacts" / "inst" / "review"
    lines = [
        json.loads(l)
        for l in (art / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    contents = [e.get("content") for e in lines]
    assert "FIRST attempt" in contents, (
        "the first executor act must survive; <role>.md alone loses it"
    )
    assert "SECOND attempt" in contents

    # act_index lets a reader tell the two executor acts apart.
    indexes = {e.get("act_index") for e in lines}
    assert len(indexes) == 3, f"each act must be distinguishable, got {indexes}"


def test_a_message_without_a_trajectory_still_serialises():
    """Non-tool agents have no turns; the record is their single response.

    The field must default to an empty list rather than be required, or every
    planner/reviewer message would need to construct one.
    """
    msg = AgentMessage(sender="planner", receiver="orchestrator", content="plan")
    payload = msg.to_dict()
    assert payload["trajectory"] == []
    assert payload["truncated"] is False


# ------------------------------------------------------------- provider differences
def test_reasoning_is_read_under_every_field_name_providers_use():
    """Providers disagree on the name AND the shape; all must be read.

    Reading only ``reasoning_content`` silently discarded the reasoning of any
    provider that spelled it differently -- and because the field is optional, the
    loss was invisible: an empty string looks identical to "the model did not
    think".
    """
    assert _extract_reasoning(_FakeMessage(reasoning_content="a")) == "a"
    assert _extract_reasoning(_FakeMessage(reasoning="b")) == "b"
    assert _extract_reasoning(_FakeMessage(thinking="c")) == "c"
    # A list of parts (some gateways stream it that way).
    assert _extract_reasoning(_FakeMessage(reasoning_content=["x", "y"])) == "xy"
    # A nested dict.
    assert _extract_reasoning(_FakeMessage(reasoning_content={"content": "z"})) == "z"
    # Absent entirely: empty, not an error.
    assert _extract_reasoning(_FakeMessage()) == ""
