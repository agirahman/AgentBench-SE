"""The two enforcement holes the partner audit found must stay closed.

docs/AUDIT_RUN_TESTS_PARTNER.md §2 and §5c, audited 2026-10-01:

1. ``execute_tool`` did not check the ROLE. The provider is only sent the schemas in
   ``AGENT_TOOLS`` -- a filter on what the model is OFFERED, not on what can run.
   Calling ``execute_tool("write_file", ...)`` succeeded for a reviewer. So the
   review strategy's premise ("the reviewer does not author code") rested on the
   model never guessing a tool name it was not shown.

2. ``run_tests`` executes an arbitrary shell command (``shell=True``), so a
   read-only role could write through it. The audit proved constructively that a
   reviewer write reaches the SHIPPED patch on the revision path
   (``review_strategy.py:201`` captures the diff AFTER the reviewer act).

Neither had actually happened in the two pilots measured (0 of 20 reviewer calls
were writes), so these are exploitable holes, not observed failures. They are worth
closing before a 150-run sweep because a single occurrence would silently put
reviewer-authored code into the results.

The classifier is validated against negative controls FIRST, because an earlier
attempt of mine flagged every pytest call as a write by treating ``2>&1`` as a file
redirect, and the partner's first attempt produced 13 false writes from ``->`` inside
a printed string.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents import tools as T  # noqa: E402


# ------------------------------------------------ the write classifier itself
@pytest.mark.parametrize("command", [
    "python -m pytest tests/file_storage/tests.py -q 2>&1 | tail -20",
    "python -m pytest tests/ordering/tests.py -x -q >/dev/null 2>&1",
    "cd tests && python runtests.py",
    "python -c \"print('OLD ->', x)\"",                 # '->' inside a string
    "python -c \"import ast; ast.parse(open('x.py').read())\"",
    "python -c \"import re; op=re.compile(r'(.*)')\"",
    "git rev-parse --show-toplevel",
    "git diff --stat",
    "ls -la",
    "echo hello",
    "python << 'EOF'\nprint(1)\nEOF",
    # Scratch probes OUTSIDE the repo. A first version of the guard refused the
    # first of these on a real run (EXP-20260930-398) -- a reviewer writing a probe
    # script to /tmp, which is legitimate verification, not a repo write.
    "cd /tmp && cat > t.py <<'EOF'\nimport django\nprint(django.VERSION)\nEOF",
    "cd /tmp && python -c \"open('probe.py','w').write('x')\"",
    "python -c \"open('/tmp/probe.py','w').write('x')\"",
    "echo test > /tmp/out.txt",
    "cd $TMPDIR && sed -i 's/a/b/' probe.py",
])
def test_read_only_probes_are_not_treated_as_writes(command):
    """NEGATIVE CONTROLS. Flagging these would break legitimate inspection.

    Every one of these appears in real recorded runs. A guard that refused them
    would stop the reviewer from verifying anything -- a worse outcome than the
    hole it was meant to close.
    """
    assert T._command_looks_like_a_write(command) == "", (
        f"a read-only command was classified as a write: {command!r}"
    )


@pytest.mark.parametrize("command", [
    "python -c \"open('core.py','w').write('x')\"",
    "python -c \"open('core.py','a').write('x')\"",
    "python -c \"import os; os.remove('f.py')\"",
    "python -c \"import shutil; shutil.rmtree('tests')\"",
    "sed -i 's/a/b/' file.py",
    "git checkout -- .",
    "git apply evil.patch",
    "git reset --hard",
    "echo 'x = 1' > django/conf/global_settings.py",
    "echo x >> file.py",
    "rm -rf tests",
    "cp a.py b.py",
    "patch -p1 < evil.diff",
])
def test_actual_writes_are_detected(command):
    """POSITIVE CONTROLS. These must be refused for a read-only role."""
    assert T._command_looks_like_a_write(command), (
        f"a mutating command was NOT detected: {command!r}"
    )


# ------------------------------------------------------ hole 1: role dispatch
def test_execute_tool_refuses_a_tool_outside_the_role_grant(tmp_path):
    """A reviewer must not be able to call write_file, even if it asks.

    Before the fix this returned '[ok] created ...' -- the schema filter is not
    enforcement.
    """
    T.set_repo_root(tmp_path)
    try:
        out = T.execute_tool("write_file", {"path": "evil.py", "content": "x = 1"},
                             role="reviewer")
        assert out.startswith("[error]"), (
            f"a reviewer was allowed to write: {out!r}"
        )
        assert "not available to role" in out
        assert not (tmp_path / "evil.py").exists(), "the file must not be created"

        out = T.execute_tool("edit_file",
                             {"path": "a.py", "old_string": "a", "new_string": "b"},
                             role="reviewer")
        assert out.startswith("[error]"), "a reviewer was allowed to edit"

        out = T.execute_tool("reset_repo", {}, role="reviewer")
        assert out.startswith("[error]"), "a reviewer was allowed to reset the repo"
    finally:
        T.set_repo_root(None)


def test_execute_tool_still_allows_a_granted_tool(tmp_path):
    """The guard must not break the tools the role IS granted."""
    T.set_repo_root(tmp_path)
    try:
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        out = T.execute_tool("read_file", {"path": "a.py"}, role="reviewer")
        assert not out.startswith("[error]"), out
        assert "x = 1" in out
    finally:
        T.set_repo_root(None)


def test_execute_tool_keeps_legacy_behaviour_without_a_role():
    """An empty role must not silently refuse everything.

    Existing callers and tests call execute_tool without a role; refusing there
    would break them for no security benefit, since the tool loop always passes it.
    """
    out = T.execute_tool("read_file", {"path": "nope.py"})
    assert not out.startswith("[error] tool"), out


def test_the_executor_may_still_write(tmp_path):
    """The guard is about ROLE, not about blocking writes."""
    T.set_repo_root(tmp_path)
    try:
        out = T.execute_tool("write_file", {"path": "ok.py", "content": "y = 2"},
                             role="executor")
        assert not out.startswith("[error]"), out
        assert (tmp_path / "ok.py").exists()
    finally:
        T.set_repo_root(None)


# --------------------------------------------- hole 2: run_tests as a shell
def test_a_read_only_role_cannot_write_through_run_tests(tmp_path):
    """The hole the audit proved constructively.

    ``run_tests`` is ``shell=True`` with an arbitrary command, so without this a
    reviewer could rewrite the file it is reviewing -- and on the revision path
    (``review_strategy.py:201``) that write reaches the shipped patch.
    """
    T.set_repo_root(tmp_path)
    T.reset_test_guard()
    try:
        target = tmp_path / "core.py"
        target.write_text("def f():\n    return 1\n", encoding="utf-8")

        out = T.run_tests(
            "python -c \"open('core.py','w').write('def f():\\n    return 999')\"",
            role="reviewer",
        )
        assert out.startswith("[error]"), f"the write was allowed: {out!r}"
        assert "read-only" in out
        assert target.read_text(encoding="utf-8") == "def f():\n    return 1\n", (
            "the file was modified despite the refusal"
        )
    finally:
        T.reset_test_guard()
        T.set_repo_root(None)


def test_a_read_only_role_can_still_run_tests(tmp_path):
    """The guard must not stop the reviewer from gathering evidence.

    The audit measured that 10 of the reviewer's 13 run_tests calls were read-only
    probes and 3 were test runs. Refusing those would cripple verification.
    """
    T.set_repo_root(tmp_path)
    T.reset_test_guard()
    try:
        out = T.run_tests("python -c \"print('probe ok')\"", role="reviewer")
        assert "probe ok" in out, f"a read-only probe was refused: {out!r}"
    finally:
        T.reset_test_guard()
        T.set_repo_root(None)


def test_the_executor_can_still_use_a_mutating_command(tmp_path):
    """The executor legitimately cleans up its own scratch files via the shell.

    Measured in EXP-20260930-332 (planning/11019): the executor removed its own
    untracked _t*.py scratch files with ``os.remove``. That is housekeeping by a
    role that may write, and must keep working.
    """
    T.set_repo_root(tmp_path)
    T.reset_test_guard()
    try:
        scratch = tmp_path / "_t.py"
        scratch.write_text("x = 1\n", encoding="utf-8")

        out = T.run_tests(
            "python -c \"import os; os.remove('_t.py')\"", role="executor"
        )
        assert not out.startswith("[error]"), out
        assert not scratch.exists(), "the executor's cleanup was blocked"
    finally:
        T.reset_test_guard()
        T.set_repo_root(None)


def test_run_tests_without_a_role_keeps_legacy_behaviour(tmp_path):
    """No role means no enforcement, so existing tests and callers still work."""
    T.set_repo_root(tmp_path)
    T.reset_test_guard()
    try:
        out = T.run_tests("python -c \"print('legacy')\"")
        assert "legacy" in out, out
    finally:
        T.reset_test_guard()
        T.set_repo_root(None)
