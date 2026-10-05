"""Every role that runs with tools must HAVE a tool prompt, and no prompt may
reference the dead passive-context feature.

Two defects this pins, both found in the 15-run pilot EXP-20260930-332:

1. A MISSING tool prompt fell back to the non-tool prompt SILENTLY.

   ``planner`` had no ``planner_tools.md``, so in tool-calling mode it received
   ``planner.md`` -- which says "Output ONLY valid JSON". The planner then made ZERO
   tool calls in 6 of 9 runs. It was not a strategy choosing not to explore; nothing
   asked it to explore. The results carried no mark of the difference, so the
   behaviour read as the agent's own.

   The fallback still exists (a missing file must not crash a 150-run sweep) but it
   now warns and names the file.

2. Four prompts referenced "SOURCE CODE (base commit)", a passive-context feature
   that injects NOTHING.

   ``Issue.to_agent_prompt()`` always returns the bare problem statement, and
   ``_warn_if_source_context_enabled`` (src/config.py:260-280) states the flag has
   no effect. The prompts told the model to use file paths "provided below" that
   never appear -- an invitation to invent paths, not merely a useless sentence.
"""

import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

PROMPTS = ROOT / "src" / "prompts"


# ------------------------------------------------------- tool prompt coverage
def test_every_tool_using_role_has_a_tool_prompt():
    """The invariant: a role that can call tools must have a prompt that says so.

    Read from the agent classes, so this cannot drift from the code it describes.
    """
    from agents import direct_agent, executor_agent, planner_agent, reviewer_agent

    missing = []
    for mod, cls_name in (
        (direct_agent, "DirectAgent"),
        (planner_agent, "PlannerAgent"),
        (executor_agent, "ExecutorAgent"),
        (reviewer_agent, "ReviewerAgent"),
    ):
        klass = getattr(mod, cls_name, None)
        if klass is None:
            continue
        base = klass.prompt_file
        stem = base[:-3] if base.endswith(".md") else base
        variant = PROMPTS / f"{stem}_tools.md"
        if not variant.exists():
            missing.append(f"{klass.name} (needs {variant.name})")

    assert not missing, (
        f"role(s) would silently fall back to a non-tool prompt: {missing}. "
        f"A non-tool prompt tells the model to emit JSON instead of calling tools, "
        f"so the agent does not explore and the run looks like a strategy choice."
    )


def test_a_missing_tool_prompt_warns_instead_of_falling_back_silently():
    """The fallback must be LOUD.

    It exists so a missing file cannot crash a long sweep, but silence is what let
    the planner defect survive into a pilot: nothing in the run marked the
    difference between "the agent chose not to read code" and "the agent was told
    to output JSON".
    """
    from agents.base import BaseAgent

    class _Agent(BaseAgent):
        name = "ghost"
        prompt_file = "does_not_exist_prompt.md"
        default_template = "base template"

        def _render(self, task, context, template):
            return template

    agent = _Agent.__new__(_Agent)
    agent.prompt_file = "does_not_exist_prompt.md"
    agent.template = "base template"
    agent._tool_template_cache = None

    with pytest.warns(UserWarning, match="PROMPT VARIANT MISSING"):
        result = agent._tool_template()

    # It still returns something usable -- a warning, not a crash.
    assert result == "base template"


def test_a_present_tool_prompt_does_not_warn():
    """No false alarm when the variant exists."""
    from agents.base import BaseAgent

    class _Agent(BaseAgent):
        name = "planner"
        prompt_file = "planner.md"
        default_template = ""

        def _render(self, task, context, template):
            return template

    agent = _Agent.__new__(_Agent)
    agent.prompt_file = "planner.md"
    agent.template = "base"
    agent._tool_template_cache = None

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning fails the test
        result = agent._tool_template()

    assert "read-only tools" in result.lower() or "grep" in result.lower(), (
        "the planner's tool prompt must actually instruct exploration"
    )


def test_no_false_alarm_when_the_loader_is_stubbed(monkeypatch):
    """A stubbed loader must not be reported as a missing FILE.

    Six tests replace ``load_prompt_or_default`` with a stub that returns the
    default, to keep real prompt text out of their assertions. The warning used to
    fire on that stub, so the suite printed "executor_tools.md does not exist" for a
    file that has been in the repo since a804e66 -- and the natural reading of that
    message is "the executor is running a non-tool prompt", which would be a
    catastrophic defect. It is not. A warning that sends the reader after a
    nonexistent bug is worse than no warning, because the next real one gets ignored.
    """
    from agents.base import BaseAgent

    class _Agent(BaseAgent):
        name = "executor"
        prompt_file = "executor.md"
        default_template = "base"

        def _render(self, task, context, template):
            return template

    agent = _Agent.__new__(_Agent)
    agent.prompt_file = "executor.md"
    agent.template = "base"
    agent._tool_template_cache = None

    # Simulate the test-stub situation: the loader returns nothing, but the file is
    # on disk. The warning must stay silent.
    monkeypatch.setattr(
        "agents.base.load_prompt_or_default", lambda filename, default="": str(default)
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        agent._tool_template()


def test_the_warning_still_fires_for_a_genuinely_absent_file(monkeypatch, tmp_path):
    """The filesystem check must not silence the REAL case.

    Guards the opposite error: making the warning quiet is only correct if a truly
    missing variant still reports.
    """
    from agents import base as base_mod
    from utils import prompt_loader

    monkeypatch.setattr(prompt_loader, "PROMPT_DIR", tmp_path)
    monkeypatch.setattr(base_mod, "prompt_exists", lambda filename: False)

    class _Agent(base_mod.BaseAgent):
        name = "ghost"
        prompt_file = "ghost.md"
        default_template = "base"

        def _render(self, task, context, template):
            return template

    agent = _Agent.__new__(_Agent)
    agent.prompt_file = "ghost.md"
    agent.template = "base"
    agent._tool_template_cache = None

    with pytest.warns(UserWarning, match="PROMPT VARIANT MISSING"):
        result = agent._tool_template()

    assert result == "base", "a missing file must still return a usable template"


# ------------------------------------------------------ the planner prompt itself
def test_the_planner_tool_prompt_demands_reading_code_first():
    """The specific instruction whose absence caused 0 tool calls.

    The non-tool planner prompt says "Output ONLY valid JSON" and never tells the
    model to read anything, so the model complies by answering immediately. The
    tool prompt must make reading a REQUIRED step.
    """
    text = (PROMPTS / "planner_tools.md").read_text(encoding="utf-8")

    assert "grep" in text and "read_file" in text, (
        "the planner must be told which tools to use to find the code"
    )
    assert "BEFORE forming a hypothesis" in text, (
        "the prompt must require evidence before the hypothesis; otherwise the model "
        "answers from general knowledge and the plan is ungrounded"
    )
    assert "must be one you opened" in text, (
        "the prompt must forbid inventing file paths"
    )
    assert "Do NOT write code" in text, (
        "the planner must stay read-only in its instructions too"
    )


def test_no_prompt_references_the_dead_source_context_feature():
    """SOURCE_CONTEXT injects nothing; referencing it invites invented paths.

    ``Issue.to_agent_prompt()`` returns the bare problem statement, and
    ``_warn_if_source_context_enabled`` says so explicitly. A prompt that tells the
    model to use "the paths provided below" points at a section that never appears.
    """
    offenders = []
    for path in sorted(PROMPTS.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "SOURCE CODE" in text:
            offenders.append(path.name)

    assert not offenders, (
        f"prompt(s) reference the dead passive-context feature: {offenders}. "
        f"Nothing is injected, so the instruction points at a section that does not "
        f"exist."
    )


def test_the_shared_static_header_does_not_tell_readonly_roles_to_write_code():
    """The header is prepended to EVERY role, so it must not address only writers.

    Audited by a partner on 2026-10-01 (docs/AUDIT_PROMPTS_PARTNER.md §2.5), which
    rendered the real prompt: the planner received 7,393 chars carrying THREE
    conflicting instructions --

      1. shared_static.md: "produce a correct, minimal code change as a unified
         diff", plus a full "Hard rules for the patch field" section;
      2. planner.md (via the missing-variant fallback): "Output ONLY valid JSON";
      3. READONLY_TOOL_SYSTEM_PROMPT: "Explore with read_file / grep / list_files
         to gather evidence".

    The header is cached and identical for every role, so it cannot assume the
    reader writes code. It must state that roles differ and let the role prompt
    give the specific job.
    """
    text = (PROMPTS / "shared_static.md").read_text(encoding="utf-8")

    # It must name the read-only roles explicitly, so a reader is not left to
    # infer that "the patch field" applies to them.
    assert "read-only" in text.lower(), (
        "the shared header must say that some roles are read-only; otherwise it "
        "tells the planner and reviewer to produce a diff"
    )
    assert "planner" in text and "reviewer" in text, (
        "the header must name the read-only roles it is describing"
    )

    # The patch rules must be conditional on the role producing a patch.
    assert "If your role produces a PATCH" in text, (
        "the hunk-counting rules must be conditional, not universal"
    )

    # And the dead reference must be gone from here too.
    assert "provided source" not in text.lower(), (
        "the header still points at a source snapshot that is never injected"
    )


def test_the_shared_static_header_survived_its_trim():
    """Removing the worked examples must not remove the rules they illustrated.

    The header lost three full diff examples (5,893 -> 3,660 chars) to stop
    addressing read-only roles. The RULES must remain, because direct and executor
    still write patches in the non-tool path and their own prompts carry the rest.
    """
    text = (PROMPTS / "shared_static.md").read_text(encoding="utf-8")
    for rule in ("@@ -N,M +P,Q @@", "Count lines BEFORE", "Do NOT truncate"):
        assert rule in text, f"the shared header lost a patch rule: {rule!r}"

    # The two writer prompts must still carry their own hunk rules, since the
    # shared header no longer supplies examples.
    for name in ("direct_prompt.md", "executor.md"):
        writer = (PROMPTS / name).read_text(encoding="utf-8")
        assert "@@ -N,M" in writer or "hunk header" in writer.lower(), (
            f"{name} writes patches but has no hunk-counting rule of its own"
        )


def test_the_source_context_feature_really_is_dead():
    """Confirm the premise of the test above, so it cannot rot.

    If source context is ever re-enabled, this test fails and the previous one must
    be revisited rather than deleted.
    """
    import inspect

    from models import issue as issue_mod

    source = inspect.getsource(issue_mod)
    assert "build_source_context" not in source.split("to_agent_prompt")[-1][:400], (
        "to_agent_prompt now appears to inject source context; the prompts may "
        "legitimately reference it again -- revisit the test above"
    )
