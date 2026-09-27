"""The tool loop's wrap-up nudge must match the role's mandate.

Measured on EXP-20260927-007: the "you are almost out of tool budget" message
told every role to "apply your fix NOW with edit_file". Read-only roles (planner,
reviewer) have no edit_file tool, so that instruction is impossible to obey and
invites the model to narrate an edit instead of returning its verdict.

The loop is exercised with a fake client so no network call happens; the test
asserts on the messages the loop actually sent.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from providers import tool_loop  # noqa: E402


class _FakeFunction:
    def __init__(self, name="read_file", arguments="{}"):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self):
        self.id = "call-1"
        self.function = _FakeFunction()


class _FakeMessage:
    def __init__(self, tool_calls):
        self.content = ""
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, msg):
        self.message = msg


class _FakeUsage:
    prompt_tokens = 1
    completion_tokens = 1
    total_tokens = 2


class _FakeResponse:
    def __init__(self, msg):
        self.choices = [_FakeChoice(msg)]
        self.usage = _FakeUsage()


class _FakeCompletions:
    def __init__(self, recorder, always_tool):
        self._recorder = recorder
        self._always_tool = always_tool

    def create(self, **kwargs):
        self._recorder.append(kwargs["messages"])
        if self._always_tool:
            return _FakeResponse(_FakeMessage([_FakeToolCall()]))
        return _FakeResponse(_FakeMessage(None))


class _FakeClient:
    def __init__(self, always_tool):
        self.recorder = []
        self.chat = type("Chat", (), {})()
        self.chat.completions = _FakeCompletions(self.recorder, always_tool)


def _run(role, always_tool, monkeypatch):
    client = _FakeClient(always_tool)
    monkeypatch.setattr(tool_loop, "execute_tool", lambda name, args: "ok")
    tool_loop.run_tool_loop(
        client,
        model="fake",
        prompt="fix it",
        role=role,
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
        max_tool_turns=2,
        repo_root=None,
    )
    return client


def test_readonly_role_is_never_told_to_edit(monkeypatch):
    client = _run("reviewer", always_tool=True, monkeypatch=monkeypatch)
    nudges = [
        m["content"]
        for msgs in client.recorder
        for m in msgs
        if isinstance(m.get("content"), str) and "almost out of tool budget" in m["content"]
    ]
    assert nudges, "the loop should have sent a wrap-up nudge"
    for n in nudges:
        assert "edit_file" not in n
        assert "final answer" in n


def test_editing_role_is_told_to_apply_the_fix(monkeypatch):
    client = _run("executor", always_tool=True, monkeypatch=monkeypatch)
    nudges = [
        m["content"]
        for msgs in client.recorder
        for m in msgs
        if isinstance(m.get("content"), str) and "almost out of tool budget" in m["content"]
    ]
    assert nudges, "the loop should have sent a wrap-up nudge"
    assert any("edit_file" in n for n in nudges)
