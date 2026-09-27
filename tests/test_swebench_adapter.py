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


def test_apply_status_corrupt_patch_is_not_applyable(tmp_path, monkeypatch):
    # Regression: git exits 128 with "corrupt patch". That must NOT be treated as
    # inconclusive (None), which previously promoted corrupt patches to APPLYABLE.
    import subprocess

    from experiments import swebench_adapter as sa

    _write(tmp_path, "app.py", 'print("old")\n')

    class _Proc:
        returncode = 128
        stderr = b"error: corrupt patch at line 11\n"
        stdout = b""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())
    assert sa._strict_git_apply_ok("diff", tmp_path) is False


def test_apply_status_not_a_repo_is_inconclusive(tmp_path, monkeypatch):
    # A genuine "git cannot answer" signal stays inconclusive rather than FAIL.
    import subprocess

    from experiments import swebench_adapter as sa

    class _Proc:
        returncode = 128
        stderr = b"fatal: not a git repository (or any of the parent directories)\n"
        stdout = b""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())
    assert sa._strict_git_apply_ok("diff", tmp_path) is None


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
