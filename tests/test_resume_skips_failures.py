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
