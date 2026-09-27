"""A diff must never be `.strip()`ed: it deletes a significant trailing line.

A hunk's final line may legitimately be whitespace-only context (" "), and the
diff's terminating newline is also significant. ``str.strip()`` removes both.
The result is a patch whose body is one line short of its ``@@`` header, which
git rejects with "corrupt patch at line N" — and which ``normalize_patch_headers``
then "repairs" by rewriting the header to match the truncated body, producing a
syntactically valid patch that no longer applies to the target file.

Observed on EXP-20260927-008 (django-11001 review): the raw captured diff was
2101 bytes with no trailing newline and failed with "corrupt patch at line 45";
after header normalisation the submitted 2017-byte patch failed with
"patch failed: tests/queries/tests.py:1661 / patch does not apply". The submitted
patch was therefore never applyable, and the instance could not resolve.

review_strategy.py used ``initial_diff.strip()`` and ``bb.patch.strip()``. The
tests below pin the invariant so it cannot come back.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from experiments.swebench_adapter import _check_patch_syntax  # noqa: E402

# A minimal but complete diff whose LAST hunk line is whitespace-only context.
DIFF_WITH_TRAILING_CONTEXT = (
    "diff --git a/pkg/mod.py b/pkg/mod.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/pkg/mod.py\n"
    "+++ b/pkg/mod.py\n"
    "@@ -1,4 +1,4 @@\n"
    " line one\n"
    " line two\n"
    "-old value\n"
    "+new value\n"
    " \n"
)


def test_stripping_a_diff_breaks_its_hunk_arithmetic():
    """The regression itself: strip() must be shown to corrupt the diff."""
    assert _check_patch_syntax(DIFF_WITH_TRAILING_CONTEXT) is None, "fixture must start valid"

    corrupted = DIFF_WITH_TRAILING_CONTEXT.strip()
    # strip() removed the whitespace-only context line AND the trailing newline.
    assert not corrupted.endswith("\n")
    assert _check_patch_syntax(corrupted) is not None, (
        "stripping a diff must be detectable as invalid — if this ever passes, the "
        "invariant this file documents no longer holds"
    )


def test_review_strategy_never_strips_diffs():
    """Guard the source: review_strategy must not call .strip() on a diff value."""
    src = (
        Path(__file__).resolve().parent.parent / "src" / "strategies" / "review_strategy.py"
    ).read_text(encoding="utf-8")
    for banned in ("initial_diff.strip()", "bb.patch.strip()"):
        assert banned not in src, (
            f"{banned} corrupts the diff: it deletes a whitespace-only context line "
            "and the terminating newline. Compare with .strip() for emptiness instead."
        )
