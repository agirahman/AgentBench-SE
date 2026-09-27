from experiments.swebench_adapter import (
    APPLYABLE,
    NEEDS_FUZZ,
    NOT_APPLYABLE,
    UNKNOWN,
    extract_diff,
    validate_applicability,
)


def test_extract_diff_from_python_fenced_block():
    response = """Here is the proposed fix:
```python
diff --git a/app.py b/app.py
index 1111111..2222222 100644
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-print("old")
+print("new")
```"""

    result = extract_diff(response)

    assert result.status == "VALID"
    assert "diff --git" in result.patch
    assert "print(\"new\")" in result.patch


def test_extract_diff_marks_truncated_responses():
    result = extract_diff("partial patch", finish_reason="length")

    assert result.status == "TRUNCATED"
    assert result.patch == ""


# ---------------------------------------------------------------------------
# validate_applicability — semantic check independent of diff arithmetic
# ---------------------------------------------------------------------------

def _diff(path: str, old: str, new: str, start: int = 1) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -{start} +{start} @@\n"
        f"-{old}\n"
        f"+{new}\n"
    )


def _write(tmp_path, rel: str, content: str):
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def test_apply_status_applyable_when_removed_line_exists(tmp_path):
    _write(tmp_path, "app.py", 'first\nprint("old")\nlast\n')
    verdict = validate_applicability(_diff("app.py", 'print("old")', 'print("new")'), tmp_path)
    assert verdict in (APPLYABLE, NEEDS_FUZZ)


def test_apply_status_not_applyable_when_removed_line_absent(tmp_path):
    # The removed line never appears in the file: this is the sympy-12236 shape,
    # where a model guessed the file contents. Fuzz cannot invent a removed line.
    _write(tmp_path, "app.py", "totally\ndifferent\ncontent\n")
    verdict = validate_applicability(
        _diff("app.py", 'print("this never existed")', 'print("new")'), tmp_path
    )
    assert verdict == NOT_APPLYABLE


def test_apply_status_not_applyable_when_target_file_missing(tmp_path):
    verdict = validate_applicability(_diff("missing.py", "a", "b"), tmp_path)
    assert verdict == NOT_APPLYABLE


def test_apply_status_unknown_without_repo_root():
    assert validate_applicability(_diff("app.py", "a", "b"), None) == UNKNOWN


def test_apply_status_unknown_for_empty_patch(tmp_path):
    assert validate_applicability("", tmp_path) == UNKNOWN
    assert validate_applicability("   \n  ", tmp_path) == UNKNOWN


def test_apply_status_handles_new_file_patch(tmp_path):
    # A pure addition has no removed lines to verify; it must not crash and must
    # not be reported as NOT_APPLYABLE on that basis alone.
    patch = (
        "diff --git a/new.py b/new.py\n"
        "--- /dev/null\n"
        "+++ b/new.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+import os\n"
        "+print(os.getcwd())\n"
    )
    verdict = validate_applicability(patch, tmp_path)
    assert verdict in (APPLYABLE, NEEDS_FUZZ, UNKNOWN)


def test_apply_status_unknown_for_unparseable_patch(tmp_path):
    assert validate_applicability("not a diff at all\n", tmp_path) == UNKNOWN


def test_apply_status_not_applyable_when_target_file_absent(tmp_path):
    # Patch edits a path that does not exist at base_commit: cannot ever apply.
    _write(tmp_path, "present.py", "x = 1\n")
    patch = _diff("absent/module.py", "x = 1", "x = 2")
    assert validate_applicability(patch, tmp_path) == NOT_APPLYABLE


def _init_repo(path):
    """Create a real git repo with a committed file, for git-backed checks."""
    import subprocess

    def g(*a):
        return subprocess.run(["git", *a], cwd=str(path), capture_output=True, timeout=60)

    g("init", "-q")
    g("config", "user.email", "t@t.t")
    g("config", "user.name", "t")
    _write(path, "app.py", 'print("old")\n')
    g("add", ".")
    g("commit", "-qm", "init")


def test_apply_status_corrupt_patch_is_not_applyable(tmp_path):
    # Regression: git exits 128 with "corrupt patch". That must NOT be treated as
    # inconclusive (None), which previously promoted corrupt patches to APPLYABLE.
    from experiments import swebench_adapter as sa

    _init_repo(tmp_path)
    corrupt = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,1 +1,1 @@\n"
        " this is not a valid hunk body\n"
    )
    assert sa._strict_git_apply_ok(corrupt, tmp_path) is False


def test_apply_status_not_a_repo_is_inconclusive(tmp_path):
    # A genuine "git cannot answer" signal stays inconclusive rather than FAIL.
    #
    # Also guards a real trap: git walks UP the directory tree, so a non-repo
    # path can silently resolve to an unrelated parent repository (measured on
    # this machine: a temp dir resolved to C:/Users/<user>). Validating against
    # the wrong repo yields a confident but meaningless verdict.
    from experiments import swebench_adapter as sa

    _write(tmp_path, "app.py", 'print("old")\n')
    valid = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,1 +1,1 @@\n"
        '-print("old")\n'
        '+print("new")\n'
    )
    assert sa._strict_git_apply_ok(valid, tmp_path) is None


def test_apply_status_removed_line_present_elsewhere_is_needs_fuzz(tmp_path):
    # Line exists in the file but not at the hunk's claimed offset: fuzz may
    # still place it, so this must not be a hard NOT_APPLYABLE.
    _write(tmp_path, "app.py", "aaa\nbbb\nccc\n")
    patch = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        "-ccc\n"
        "+zzz\n"
    )
    verdict = validate_applicability(patch, tmp_path)
    assert verdict in (NEEDS_FUZZ, APPLYABLE)


# ---------------------------------------------------------------------------
# Trailing whitespace-only context line
# ---------------------------------------------------------------------------
#
# Under edit-then-diff the patch comes verbatim from `git diff`, so a false
# HUNK_MISMATCH would mislabel perfect patches as NORMALIZE and corrupt the
# patch-validity metric. `str.strip()` used to eat the final " " context line,
# counting the hunk one line short: measured (4, 4) instead of (5, 5).


def _git_diff_with_trailing_blank_context():
    """Return a real git diff whose hunk ends with a whitespace-only line."""
    import subprocess
    import tempfile
    from pathlib import Path

    repo = Path(tempfile.mkdtemp()) / "r"
    repo.mkdir()

    def g(*a, **kw):
        return subprocess.run(
            ["git", *a], cwd=str(repo), capture_output=True, timeout=60, **kw
        )

    g("init", "-q")
    g("config", "user.email", "t@t.t")
    g("config", "user.name", "t")
    (repo / "f.py").write_text("a\nb\n\nc\n\n", encoding="utf-8")
    g("add", ".")
    g("commit", "-qm", "init")
    (repo / "f.py").write_text("a\nb\n\nX\n\n", encoding="utf-8")
    return g("diff", "--no-color").stdout.decode("utf-8", "replace")


def test_git_diff_with_trailing_blank_context_is_valid():
    from experiments import swebench_adapter as sa

    diff = _git_diff_with_trailing_blank_context()

    # The final context line is " " and must survive trimming.
    assert diff.split("\n")[-2] == " ", "fixture no longer exercises the bug"
    assert sa._check_patch_syntax(diff) is None
    assert extract_diff(diff).status == "VALID"


def test_strip_blank_edges_keeps_whitespace_only_line():
    from experiments import swebench_adapter as sa

    # Leading/trailing empty lines go, but a " " context line is significant.
    assert sa._strip_blank_edges("\n\nx\n \n\n") == "x\n "
    assert sa._strip_blank_edges("") == ""
    assert sa._strip_blank_edges("\n\n") == ""
