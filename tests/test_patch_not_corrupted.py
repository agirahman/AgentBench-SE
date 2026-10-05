"""A diff must never be newline-unescaped, and never tolerate an unprefixed line.

Two independent defects, both measured on EXP-20260928-003, combined to submit
patches that could not apply:

1. ``_normalize_newlines`` rewrote EVERY literal ``\\n`` (backslash + n) into a
   real line break, including the ones inside the source code a diff carries.
   ``django/forms/widgets.py`` line 80 is ``return mark_safe('\\n'.join(...))``;
   after the rewrite it became two lines, the second one losing its diff prefix.

2. ``_check_patch_syntax`` accepted a hunk-body line with no prefix (it fell
   through a ``pass`` branch and was counted as context). So the patch produced
   by (1) was labelled VALID, or "repaired" to NORMALIZE by
   ``normalize_patch_headers`` — which rewrote the ``@@`` header to match the
   now-truncated body. Both labels count as valid patches in the statistics.

Combined effect: the raw ``git diff`` in ``artifacts/<id>/<strategy>/patch.txt``
applied cleanly, while the patch actually submitted under ``patches/`` failed
with "corrupt patch at line N". Four patches were condemned as NOT_APPLYABLE /
NEEDS_FUZZ by the pipeline's own damage.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from experiments import swebench_adapter as sa  # noqa: E402
from experiments.swebench_adapter import extract_diff  # noqa: E402

# A source file whose text legitimately contains the two characters  \  n
# inside a Python string literal — exactly the django/forms/widgets.py shape.
# The edited line sits right below it, so the literal appears as CONTEXT in the
# diff: an untouched line that the pipeline must carry through verbatim.
SOURCE_WITH_LITERAL_BACKSLASH_N = (
    "def render(self):\n"
    "    return mark_safe('\\n'.join(chain.from_iterable(items)))\n"
    "    return 1\n"
)


def _make_repo_with_literal_backslash_n(tmp_path):
    """A real repo whose committed file contains a literal backslash-n."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def g(*a, **kw):
        return subprocess.run(
            ["git", *a], cwd=str(repo), capture_output=True, timeout=60, **kw
        )

    g("init", "-q")
    g("config", "user.email", "t@t.t")
    g("config", "user.name", "t")
    (repo / "widgets.py").write_text(SOURCE_WITH_LITERAL_BACKSLASH_N, encoding="utf-8")
    g("add", ".")
    g("commit", "-qm", "base")

    # Edit an unrelated line, so the literal backslash-n stays as CONTEXT.
    (repo / "widgets.py").write_text(
        SOURCE_WITH_LITERAL_BACKSLASH_N.replace("return 1", "return 2"),
        encoding="utf-8",
    )
    return repo, g("diff", "--no-color").stdout.decode("utf-8")


def test_git_diff_containing_literal_backslash_n_survives_extract_diff(tmp_path):
    """The regression: a valid git diff must come out byte-identical."""
    repo, diff = _make_repo_with_literal_backslash_n(tmp_path)

    # Sanity: the fixture really does exercise the trap.
    assert "mark_safe('\\n'.join" in diff, "fixture no longer contains the literal"

    result = extract_diff(diff)

    assert result.patch == diff, (
        "extract_diff must not rewrite a diff that already has line structure: "
        "unescaping turns the literal \\n inside the source line into a real "
        "line break, which orphans the rest of the line and makes git report "
        "'corrupt patch'"
    )

    # And it must still apply.
    (repo / "widgets.py").write_text(SOURCE_WITH_LITERAL_BACKSLASH_N, encoding="utf-8")
    chk = subprocess.run(
        ["git", "apply", "--check", "-"],
        cwd=str(repo), input=result.patch.encode(), capture_output=True, timeout=60,
    )
    assert chk.returncode == 0, chk.stderr.decode(errors="replace")


def test_normalize_newlines_keeps_real_diffs_untouched():
    real_diff = (
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,3 +1,3 @@\n"
        " keep = '\\n'\n"
        "-old\n"
        "+new\n"
    )
    assert sa._normalize_newlines(real_diff) == real_diff


def test_normalize_newlines_still_unescapes_a_collapsed_json_patch():
    """The legacy path it was written for must keep working."""
    collapsed = "diff --git a/x.py b/x.py\\n--- a/x.py\\n+++ b/x.py\\n@@ -1 +1 @@\\n-a\\n+b"
    out = sa._normalize_newlines(collapsed)
    assert "\n@@ -1 +1 @@\n" in out
    assert out.startswith("diff --git a/x.py b/x.py\n")


def test_check_patch_syntax_rejects_unprefixed_body_line():
    """A body line with no prefix cannot come from git and can never apply."""
    corrupt = (
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,3 +1,3 @@\n"
        " line one\n"
        "'.join(orphaned_remainder)\n"
        "+added\n"
        " line three\n"
    )
    assert sa._check_patch_syntax(corrupt) == "BAD_BODY"


def test_check_patch_syntax_rejects_a_split_string_literal_end_to_end():
    """The full EXP-20260928-003 shape, built without touching the filesystem."""
    corrupt = (
        "diff --git a/widgets.py b/widgets.py\n"
        "--- a/widgets.py\n"
        "+++ b/widgets.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def render(self):\n"
        "     return mark_safe('\n"
        "'.join(items))\n"
        "+    # touched\n"
    )
    # Must NOT be reported as VALID / NORMALIZE: those count as valid patches.
    assert sa._check_patch_syntax(corrupt) == "BAD_BODY"
    result = extract_diff(corrupt)
    assert result.status == "BAD_BODY"
    assert result.patch == ""


def test_normalize_patch_headers_cannot_repair_an_unprefixed_line():
    """Header rewriting must not launder corruption into a NORMALIZE label."""
    corrupt = (
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,3 +1,3 @@\n"
        " line one\n"
        "'.join(orphaned)\n"
        "+added\n"
    )
    fixed = sa.normalize_patch_headers(corrupt)
    assert sa._check_patch_syntax(fixed) == "BAD_BODY"
