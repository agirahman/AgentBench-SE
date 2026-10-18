"""--resume must RETRY an instance that failed, not treat it as done.

p7's audit found that an errored row is appended to the strategy jsonl with the
same resume keys as a success (runner.py:452-465), and _load_existing_ids
(runner.py:106-129) reads every line without filtering patch_status. So the
resume key is "already done" and the instance is skipped forever.

This is the third time --resume has been implicated: ee145e7 fixed it minting a
new directory every time, and now the skip logic itself treats failures as
successes. A resume feature that silently keeps the failures is worse than none,
because it looks like it worked.

These tests pin the intended behaviour: a FAILED run must not count as done.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from experiments.runner import _load_existing_ids, _resume_key  # noqa: E402


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def test_successful_row_is_treated_as_done(tmp_path):
    p = tmp_path / "direct.jsonl"
    _write(p, [{
        "instance_id": "django__django-11001",
        "model_name_or_path": "m",
        "thinking": False,
        "patch_status": "VALID",
        "model_patch": "diff --git a b",
    }])
    assert _load_existing_ids(str(p)) == {_resume_key("django__django-11001", "m", False)}


def test_errored_row_must_not_be_treated_as_done(tmp_path):
    """The bug: a 502 leaves an error row that blocks its own retry."""
    p = tmp_path / "review.jsonl"
    _write(p, [{
        "instance_id": "django__django-11019",
        "model_name_or_path": "m",
        "thinking": False,
        "patch_status": "TIMEOUT",
        "error_type": "InternalServerError",
        "error_message": "Error code: 502 - bad_gateway",
        "model_patch": "",
    }])
    done = _load_existing_ids(str(p))
    assert done == set(), (
        "an instance that died (empty patch, error recorded) must be retried by "
        f"--resume, but it was treated as done: {done}"
    )


def test_mixed_file_keeps_only_the_successes(tmp_path):
    p = tmp_path / "planning.jsonl"
    _write(p, [
        {"instance_id": "A", "model_name_or_path": "m", "thinking": False,
         "patch_status": "VALID", "model_patch": "diff"},
        {"instance_id": "B", "model_name_or_path": "m", "thinking": False,
         "patch_status": "TIMEOUT", "model_patch": ""},
        {"instance_id": "C", "model_name_or_path": "m", "thinking": False,
         "patch_status": "VALID", "model_patch": "diff2"},
    ])
    assert _load_existing_ids(str(p)) == {
        _resume_key("A", "m", False),
        _resume_key("C", "m", False),
    }


def test_empty_patch_without_status_is_not_done(tmp_path):
    """Defence in depth: an empty patch is not a result even if unlabelled."""
    p = tmp_path / "direct.jsonl"
    _write(p, [
        {"instance_id": "A", "model_name_or_path": "m", "thinking": False,
         "model_patch": ""},
    ])
    assert _load_existing_ids(str(p)) == set()


@pytest.mark.parametrize(
    "status,error_type",
    [
        ("PROVIDER_ERROR", "InternalServerError"),
        ("INTERRUPTED", "KeyboardInterrupt"),
    ],
)
def test_infra_death_then_success_is_done(tmp_path, status, error_type):
    """MIXED CASE (the regression): an infra death BEFORE a real success must not
    re-run the success.

    Shape taken verbatim from production, not invented:

      * EXP-20261012-005 direct.jsonl -- row 8 = matplotlib__matplotlib-23563
        PROVIDER_ERROR / InternalServerError, patchlen 0; row 11 = SAME key,
        VALID, patchlen 928.
      * EXP-20261002-542 review.jsonl -- row 3 = django__django-11001
        INTERRUPTED / KeyboardInterrupt, patchlen 0; row 4 = SAME key, VALID,
        patchlen 552.

    The erroneous fix tested infrastructure BEFORE the patch, so the death row won
    and the key looked unfinished: --resume re-ran work that was already paid for,
    and because the evaluator dedups on the LAST row (tools/eval_modal.py), a worse
    retry could OVERWRITE the good VALID row. Here the success is the later row, so
    the retry is not even overwriting -- it is pure waste.

    Rule 1 of the precedence contract: a non-empty patch on a non-infra row makes
    the key DONE, regardless of any earlier death. Reverting the two checks (infra
    first) turns this test red.
    """
    p = tmp_path / "direct.jsonl"
    _write(p, [
        {"instance_id": "matplotlib__matplotlib-23563",
         "model_name_or_path": "cbai/deepseek-v4.1-flash", "thinking": True,
         "patch_status": status, "error_type": error_type, "model_patch": ""},
        {"instance_id": "matplotlib__matplotlib-23563",
         "model_name_or_path": "cbai/deepseek-v4.1-flash", "thinking": True,
         "patch_status": "VALID", "model_patch": "diff --git a/x b/x" * 20},
    ])
    key = _resume_key("matplotlib__matplotlib-23563", "cbai/deepseek-v4.1-flash", True)
    done = _load_existing_ids(str(p))
    assert done == {key}, (
        f"a {status} row followed by a VALID patch row means the key is finished; "
        f"--resume would re-run paid work, but it was not treated as done: {done}"
    )


def test_success_then_infra_death_is_also_done(tmp_path):
    """Order-of-rows must not matter: success first, die later -- still done.

    The mirror of the case above. Once a real outcome exists, a later unrelated
    infra row (e.g. a retry that died) must not resurrect the key."""
    p = tmp_path / "direct.jsonl"
    _write(p, [
        {"instance_id": "A", "model_name_or_path": "m", "thinking": False,
         "patch_status": "VALID", "model_patch": "diff --git a/x b/x"},
        {"instance_id": "A", "model_name_or_path": "m", "thinking": False,
         "patch_status": "PROVIDER_ERROR", "error_type": "InternalServerError",
         "model_patch": ""},
    ])
    assert _load_existing_ids(str(p)) == {_resume_key("A", "m", False)}


def test_sandbox_down_then_valid_is_done(tmp_path):
    """A LATER genuine success outranks an earlier SANDBOX_DOWN row.

    Ties the two fixes together: SANDBOX_DOWN alone must stay retryable (see the
    test below), but it must not veto a real outcome recorded afterwards -- the
    re-run already happened and produced a valid patch. Rule 1 wins over rule 2.
    """
    p = tmp_path / "direct.jsonl"
    _write(p, [
        {"instance_id": "A", "model_name_or_path": "m", "thinking": False,
         "patch_status": "SANDBOX_DOWN", "model_patch": "diff --git a/contaminated b/x"},
        {"instance_id": "A", "model_name_or_path": "m", "thinking": False,
         "patch_status": "VALID", "model_patch": "diff --git a/clean b/x"},
    ])
    assert _load_existing_ids(str(p)) == {_resume_key("A", "m", False)}


def test_infra_death_without_any_success_still_uses_no_budget(tmp_path):
    """Invariant (iii): infra rows neither finish a key nor count toward the budget.

    Two infra deaths and no outcome is still "retry forever" -- the whole point of
    --resume. Guards against a fix for the mixed case that accidentally routes
    infra rows into ``_no_patch_attempts`` and retires the key after two deaths.
    """
    p = tmp_path / "direct.jsonl"
    _write(p, [
        {"instance_id": "A", "model_name_or_path": "m", "thinking": False,
         "patch_status": "TIMEOUT", "error_type": "Timeout", "model_patch": ""},
        {"instance_id": "A", "model_name_or_path": "m", "thinking": False,
         "patch_status": "PROVIDER_ERROR", "error_type": "InternalServerError",
         "model_patch": ""},
        {"instance_id": "A", "model_name_or_path": "m", "thinking": False,
         "patch_status": "SANDBOX_DOWN", "model_patch": ""},
    ])
    assert _load_existing_ids(str(p)) == set()


def test_sandbox_down_row_with_a_patch_is_still_retried(tmp_path):
    """A SANDBOX_DOWN run KEEPS its patch, and that is exactly what used to lock
    it out of --resume.

    The runner deliberately stores the patch of a sandbox-down run (runner.py
    "Sandbox-down detection", 2026-10-07) so an evaluator can still score it --
    but the run happened with NO working test environment, so the status is
    SANDBOX_DOWN, an infrastructure death (``_PATCH_STATUS_FAILED``). The loader
    tested the patch BEFORE the status, so the key landed in ``ids`` and the
    contaminated run was skipped forever, permanently poisoning the strategy
    comparison it should have been excluded from.

    Regression guard: with the status checked first, this key must NOT be
    returned. Reversing the two checks turns this test red.
    """
    p = tmp_path / "direct.jsonl"
    _write(p, [{
        "instance_id": "django__django-11019",
        "model_name_or_path": "m",
        "thinking": False,
        "patch_status": "SANDBOX_DOWN",
        "model_patch": "diff --git a/x b/x",   # kept on purpose by the runner
    }])
    done = _load_existing_ids(str(p))
    assert done == set(), (
        "a SANDBOX_DOWN run carries a patch but measured nothing; --resume must "
        f"redo it, yet it was treated as done: {done}"
    )


def test_valid_row_with_a_patch_is_still_done(tmp_path):
    """The other half of the precedence fix: a real VALID run must stay skipped.

    Checking infrastructure first must not un-retire finished work -- a VALID
    row with a patch is a genuine outcome and --resume must still skip it.
    """
    p = tmp_path / "direct.jsonl"
    _write(p, [{
        "instance_id": "django__django-11001",
        "model_name_or_path": "m",
        "thinking": False,
        "patch_status": "VALID",
        "model_patch": "diff --git a/x b/x",
    }])
    assert _load_existing_ids(str(p)) == {
        _resume_key("django__django-11001", "m", False)
    }


def test_sandbox_down_row_beside_a_valid_row_only_retries_the_contaminated_one(
    tmp_path,
):
    """Per-key decision: the shared file must not drag the healthy key along.

    Both rows live in one strategy file, so a fix that keyed off the file rather
    than the (instance x model x thinking) group would either re-run the good
    run or, worse, keep skipping the bad one.
    """
    p = tmp_path / "direct.jsonl"
    _write(p, [
        {"instance_id": "A", "model_name_or_path": "m", "thinking": False,
         "patch_status": "VALID", "model_patch": "diff --git a/x b/x"},
        {"instance_id": "B", "model_name_or_path": "m", "thinking": False,
         "patch_status": "SANDBOX_DOWN", "model_patch": "diff --git a/y b/y"},
    ])
    assert _load_existing_ids(str(p)) == {_resume_key("A", "m", False)}
