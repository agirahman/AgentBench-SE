"""Tests for the agent tool layer (agents/tools.py).

These tools had NO test coverage while they were the only code path that writes
to the filesystem — and they were the path that produced the unusable patches on
EXP-20260824-005. Every test here runs against a REAL temporary git repository,
because the behaviour under test (capture the working-tree diff, refuse paths
outside the sandbox, reset between strategies) only exists in the presence of an
actual repository.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents import tools as T  # noqa: E402


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, timeout=60
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A real git repo with one committed file, bound as the active sandbox."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t.t")
    _git(r, "config", "user.name", "t")
    (r / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (r / "sub").mkdir()
    (r / "sub" / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "init")
    monkeypatch.setattr(T.Config, "TOOLCALL_ENABLED", True, raising=False)
    T.set_repo_root(r)
    yield r
    T.set_repo_root(None)


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------

def test_refuses_to_operate_without_a_bound_repo():
    """No bound repo must NOT silently fall back to the shared sandbox base.

    Falling back would let an agent read a different instance's source, which
    contaminates the run invisibly.
    """
    T.set_repo_root(None)
    out = T.read_file("anything.py")
    assert out.startswith("[error]")
    assert "No repository is bound" in out


def test_rejects_path_escaping_the_repo(repo):
    out = T.read_file("../../etc/passwd")
    assert out.startswith("[error]")
    assert "outside the allowed repo directory" in out


def test_normalizes_git_bash_absolute_path(repo):
    """Models emit MSYS-style paths ('/d/dev/repo/file.py'); they must work.

    Observed in EXP-20260823-010: every such call was rejected and the agent
    burned turns retrying the same shape.
    """
    msys = "/" + str(repo).replace(":", "").replace("\\", "/")
    out = T.read_file(f"{msys}/app.py")
    assert out.startswith("[error]") is False
    assert "def add" in out


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------

def test_edit_file_applies_the_change(repo):
    out = T.edit_file("app.py", "    return a - b", "    return a + b")
    assert out.startswith("[ok]")
    assert "return a + b" in (repo / "app.py").read_text(encoding="utf-8")


def test_edit_file_reports_the_resulting_diff(repo):
    out = T.edit_file("app.py", "return a - b", "return a + b")
    assert "-    return a - b" in out
    assert "+    return a + b" in out


def test_edit_file_rejects_missing_old_string(repo):
    out = T.edit_file("app.py", "this text is not in the file", "x")
    assert out.startswith("[error]")
    assert "not found" in out
    # File must be untouched.
    assert "return a - b" in (repo / "app.py").read_text(encoding="utf-8")


def test_edit_file_rejects_ambiguous_match(repo):
    """An ambiguous edit must fail loudly, not pick an arbitrary occurrence.

    A mis-placed edit yields a plausible-looking but wrong patch, which is far
    worse than an error the model can react to.
    """
    (repo / "dup.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    out = T.edit_file("dup.py", "x = 1", "x = 2")
    assert out.startswith("[error]")
    assert "ambiguous" in out


def test_edit_file_rejects_identical_strings(repo):
    out = T.edit_file("app.py", "return a - b", "return a - b")
    assert out.startswith("[error]")


def test_edit_file_rejects_empty_old_string(repo):
    out = T.edit_file("app.py", "", "new content")
    assert out.startswith("[error]")
    assert "write_file" in out


def test_edit_file_handles_multiline_replacement(repo):
    out = T.edit_file(
        "app.py",
        "def add(a, b):\n    return a - b",
        "def add(a, b):\n    total = a + b\n    return total",
    )
    assert out.startswith("[ok]")
    assert "total = a + b" in (repo / "app.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------

def test_write_file_creates_new_file(repo):
    out = T.write_file("new.py", "VALUE = 42\n")
    assert out.startswith("[ok]")
    assert "created" in out
    assert (repo / "new.py").read_text(encoding="utf-8") == "VALUE = 42\n"


def test_write_file_creates_parent_directories(repo):
    out = T.write_file("pkg/deep/new.py", "X = 1\n")
    assert out.startswith("[ok]")
    assert (repo / "pkg" / "deep" / "new.py").exists()


# ---------------------------------------------------------------------------
# capture_diff — the mechanism that replaces model-typed diffs
# ---------------------------------------------------------------------------

def test_capture_diff_includes_tracked_edits(repo):
    T.edit_file("app.py", "return a - b", "return a + b")
    diff = T.capture_diff(repo)
    assert "diff --git" in diff
    assert "+    return a + b" in diff


def test_capture_diff_includes_newly_created_files(repo):
    """A fix that adds a file must not vanish.

    Plain `git diff` omits untracked files, so a new file would silently be
    dropped from the patch and the fix would look like a no-op.
    """
    T.write_file("brand_new.py", "ANSWER = 42\n")
    diff = T.capture_diff(repo)
    assert "brand_new.py" in diff
    assert "ANSWER = 42" in diff


def test_capture_diff_is_empty_on_a_clean_tree(repo):
    assert T.capture_diff(repo).strip() == ""


def test_captured_diff_actually_applies(repo, tmp_path):
    """The captured patch must be applyable by git — the entire point.

    This is the property the old text-diff mechanism could not guarantee: 31 of
    66 sampled patches on EXP-20260824-005 could never apply.
    """
    T.edit_file("app.py", "return a - b", "return a + b")
    T.write_file("added.py", "NEW = True\n")
    diff = T.capture_diff(repo)
    assert diff.strip()

    fresh = tmp_path / "fresh"
    subprocess.run(
        ["git", "clone", "-q", str(repo), str(fresh)], capture_output=True, timeout=120
    )
    _git(fresh, "checkout", "-q", "HEAD")

    check = subprocess.run(
        ["git", "apply", "--check", "-p1"],
        input=diff.encode("utf-8"),
        cwd=str(fresh),
        capture_output=True,
        timeout=60,
    )
    assert check.returncode == 0, check.stderr.decode("utf-8", "replace")


def test_capture_diff_normalizes_crlf(repo):
    """CRLF must not survive into the patch: a stray \\r breaks `git apply`."""
    T.write_file("crlf.py", "line1\r\nline2\r\n")
    diff = T.capture_diff(repo)
    assert "\r" not in diff


# ---------------------------------------------------------------------------
# reset_working_tree — isolation between strategies
# ---------------------------------------------------------------------------

def test_reset_restores_modified_files(repo):
    T.edit_file("app.py", "return a - b", "return a + b")
    T.reset_working_tree(repo)
    assert "return a - b" in (repo / "app.py").read_text(encoding="utf-8")
    assert T.capture_diff(repo).strip() == ""


def test_reset_removes_new_files(repo):
    T.write_file("scratch.py", "X = 1\n")
    T.reset_working_tree(repo)
    assert not (repo / "scratch.py").exists()


def test_reset_prevents_leakage_between_strategies(repo):
    """Two strategies on one issue must each produce their OWN diff.

    Without a reset, strategy N+1 inherits strategy N's edits and its captured
    diff contains both — silently corrupting the per-strategy comparison.
    """
    T.edit_file("app.py", "return a - b", "return a + b")
    diff_a = T.capture_diff(repo)

    T.reset_working_tree(repo)
    T.edit_file("sub/mod.py", "VALUE = 1", "VALUE = 2")
    diff_b = T.capture_diff(repo)

    assert "app.py" in diff_a
    assert "app.py" not in diff_b
    assert "sub/mod.py" in diff_b


# ---------------------------------------------------------------------------
# finalize_patch
# ---------------------------------------------------------------------------

def test_finalize_patch_prefers_the_repo_diff(repo):
    T.edit_file("app.py", "return a - b", "return a + b")
    out = T.finalize_patch(repo, "model typed some prose instead of a patch")
    assert "diff --git" in out
    assert "prose" not in out


def test_finalize_patch_falls_back_when_nothing_was_edited(repo):
    """No edit means no patch — report the model text so it is scored NO_DIFF."""
    out = T.finalize_patch(repo, "I explored the repo but changed nothing")
    assert out == "I explored the repo but changed nothing"


def test_finalize_patch_falls_back_when_toolcall_disabled(repo, monkeypatch):
    monkeypatch.setattr(T.Config, "TOOLCALL_ENABLED", False, raising=False)
    T.edit_file("app.py", "return a - b", "return a + b")
    out = T.finalize_patch(repo, "legacy text")
    assert out == "legacy text"


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

def test_planner_and_reviewer_have_no_editing_tools():
    """Read-only roles must not be handed editing tools.

    If they were, a 'review' could silently rewrite the code it is reviewing,
    which destroys the independence the review strategy depends on.
    """
    for role in ("planner", "reviewer"):
        names = {s["function"]["name"] for s in T.get_tools_for_agent(role)}
        assert "edit_file" not in names, role
        assert "write_file" not in names, role


def test_editing_roles_can_edit():
    for role in ("direct", "executor"):
        names = {s["function"]["name"] for s in T.get_tools_for_agent(role)}
        assert "edit_file" in names, role


def test_every_schema_has_a_function():
    """A schema without an implementation would make the model call a dead tool."""
    for schema in T.TOOL_SCHEMAS:
        assert schema["function"]["name"] in T.TOOL_FUNCTIONS


def test_execute_tool_unknown_name_is_an_error_not_a_crash():
    out = T.execute_tool("shell", {"command": "rm -rf /"})
    assert out.startswith("[error]")
    assert "unknown tool" in out


def test_execute_tool_bad_arguments_is_an_error_not_a_crash(repo):
    out = T.execute_tool("read_file", {"wrong_arg": 1})
    assert out.startswith("[error]")


# ---------------------------------------------------------------------------
# applyability must be judged against HEAD, not the (already edited) working tree
# ---------------------------------------------------------------------------

def test_applyability_ignores_agent_edits_in_working_tree(repo):
    """A patch captured from the agent's own edit must read as APPLYABLE.

    Regression test for a real bug: ``validate_applicability`` used to read the
    working tree. Under edit-then-diff the agent has ALREADY applied its change,
    so the line the patch removes is genuinely absent from the working tree and
    every patch was condemned as NOT_APPLYABLE by construction — while the very
    same patch applied cleanly to a pristine checkout.
    """
    from experiments.swebench_adapter import validate_applicability

    # Capture a real patch, then leave the edit in place (the run-time state).
    T.edit_file("app.py", "    return a - b", "    return a + b")
    diff = T.capture_diff(repo)
    assert diff.strip()

    # Working tree is dirty: this is exactly the condition that used to break it.
    assert (repo / "app.py").read_text(encoding="utf-8").count("return a + b") == 1

    verdict = validate_applicability(diff, repo)
    assert verdict == "APPLYABLE", verdict


def test_applyability_matches_a_pristine_checkout(repo, tmp_path):
    """The verdict must agree with `git apply --check` on a clean clone."""
    from experiments.swebench_adapter import validate_applicability

    T.edit_file("app.py", "    return a - b", "    return a + b")
    diff = T.capture_diff(repo)

    fresh = tmp_path / "pristine"
    subprocess.run(
        ["git", "clone", "-q", str(repo), str(fresh)], capture_output=True, timeout=120
    )
    ground_truth = subprocess.run(
        ["git", "apply", "--check", "-p1"],
        input=diff.encode("utf-8"), cwd=str(fresh), capture_output=True, timeout=60,
    ).returncode == 0

    assert (validate_applicability(diff, repo) == "APPLYABLE") is ground_truth


def test_applyability_still_flags_a_genuinely_bad_patch(repo):
    """The fix must not make the check vacuous: a bogus patch stays rejected."""
    from experiments.swebench_adapter import validate_applicability

    bogus = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-this line does not exist anywhere in the file\n"
        "+replacement\n"
    )
    assert validate_applicability(bogus, repo) == "NOT_APPLYABLE"
