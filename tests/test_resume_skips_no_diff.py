"""--resume must retry infrastructure deaths, not legitimate "no diff" outcomes.

Context -- two facts that pull in opposite directions:

1. A run that DIED (provider 502, rate limit, git failure) must be retried by
   --resume, or the failure is frozen into the results forever. That is why
   ``--resume`` exists.

2. A run that FINISHED and produced no diff (the model answered with prose) is an
   OUTCOME, not an interruption. Retrying it on every resume is a silent cost
   leak: measured on this repo's own history, 299 (strategy x instance)
   combinations are in that state, and every resume re-pays for them.

The distinction cannot be made on "is the patch empty?" -- BOTH cases have an
empty patch. It has to come from ``error_type``, which only the error path writes
(runner.py:987, and a repo-wide grep finds no other writer).

The compromise implemented here is option **D**: a no-diff outcome is retried
EXACTLY ONCE, then accepted as final. One retry keeps the safety net (a transient
empty answer is still recovered) while bounding the leak. Infrastructure deaths
are NOT bounded -- they retry for as long as they keep failing.

Definitions that must stay in sync with runner.py:

* a "no-patch row" = empty ``model_patch`` AND no ``error_type``
  (patch_status may be NO_DIFF/EMPTY, or absent entirely -- the absent case is
  the single most common one in real data, 2_318 rows, so it is NOT an edge case)
* the attempt count is per (instance x model x thinking) x strategy -- the same
  composite key ``_resume_key`` builds, because that is what resume looks up
"""
import json

import pytest

from experiments.runner import (
    _no_patch_attempts,
    _load_existing_ids,
    _resume_key,
)


MODEL = "test-model"
THINK = True


def _row(iid: str, **over) -> dict:
    """A row shaped like a real savepoint row."""
    row = {
        "instance_id": iid,
        "model_name_or_path": MODEL,
        "strategy": "direct",
        "thinking": THINK,
        "patch_status": "NO_DIFF",
        "model_patch": "",
    }
    row.update(over)
    return row


def _write(path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


# --------------------------------------------------------------------------
# T1 / T2 / T6 -- a no-diff outcome with no error_type is NOT an infrastructure
# death, but the FIRST attempt must still not be treated as "finished": option D
# allows one retry. So these assert on the ATTEMPT COUNT, not on a bool.
# --------------------------------------------------------------------------

def test_T1_no_diff_row_counts_as_a_no_patch_attempt():
    """T1: a NO_DIFF row (no error_type) is an outcome, and counts as attempt 1."""
    rows = [_row("django__django-1", patch_status="NO_DIFF")]
    assert _no_patch_attempts(rows) == 1


def test_T2_empty_row_counts_as_a_no_patch_attempt():
    """T2: EMPTY behaves exactly like NO_DIFF -- both are outcomes."""
    rows = [_row("django__django-1", patch_status="EMPTY")]
    assert _no_patch_attempts(rows) == 1


def test_T6_row_without_any_patch_status_is_a_no_patch_attempt():
    """T6: the status-less row is the MOST COMMON case in real data (2_318 of
    6_125 rows), not an edge case.

    Real example, verbatim from results/EXP-*/predictions/*.jsonl:
        {"instance_id": "psf__requests-1963", "model_patch": "",
         "model_name_or_path": "deepseek-v4-flash", "strategy": "direct"}
    """
    row = _row("psf__requests-1963", model_name_or_path="deepseek-v4-flash")
    del row["patch_status"]          # absent, not empty
    assert _no_patch_attempts([row]) == 1


# --------------------------------------------------------------------------
# T3 / T4 / T14 -- infrastructure deaths are NEVER bounded by option D.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["TIMEOUT", "ERROR", "FAILED", "RATE_LIMIT", "PROVIDER_ERROR"])
def test_T4_infrastructure_status_is_not_a_no_patch_attempt(status):
    """T4: every member of ``_PATCH_STATUS_FAILED`` must keep retrying.

    Note for the thesis: on this repo's real data these statuses never appear
    (only TIMEOUT does, 382 rows) because the error path derives them from the
    exception type. They are tested as a CONTRACT, not as a reproduction of an
    observed incident.
    """
    rows = [_row("django__django-1", patch_status=status, error_type="SomeError")]
    assert _no_patch_attempts(rows) == 0


def test_T3_rate_limit_with_error_type_is_not_a_no_patch_attempt():
    """T3: the rate-limit case -- the one --resume exists to recover."""
    rows = [_row("django__django-1", patch_status="RATE_LIMIT", error_type="RateLimitError")]
    assert _no_patch_attempts(rows) == 0


def test_T14_error_type_alone_decides_infrastructure_not_the_status():
    """T14 (finding A): the predicate must lean on ``error_type``, because the
    status string alone is not enough -- an unknown failure status can arrive
    with an error_type, and must still be retried.

    This guards the single point of failure: runner.py:987 is the ONLY writer of
    ``error_type`` in the repo."""
    rows = [_row("django__django-1", patch_status="SOMETHING_NEW", error_type="WeirdError")]
    assert _no_patch_attempts(rows) == 0


def test_T18_option_D_does_not_bound_infrastructure_retries():
    """T18: two infrastructure failures must NOT stop the retry -- only no-diff
    outcomes are bounded to one retry."""
    rows = [
        _row("django__django-1", patch_status="RATE_LIMIT", error_type="RateLimitError"),
        _row("django__django-1", patch_status="RATE_LIMIT", error_type="RateLimitError"),
        _row("django__django-1", patch_status="RATE_LIMIT", error_type="RateLimitError"),
    ]
    assert _no_patch_attempts(rows) == 0
    # ...and the loader must therefore NOT return this key (it stays retryable).
    assert _load_existing_ids_from_rows(rows, "django__django-1") is False


# --------------------------------------------------------------------------
# T17 -- the core of option D: exactly one retry.
# --------------------------------------------------------------------------

def _load_existing_ids_from_rows(rows, iid, tmp_name="direct") -> bool:
    """Helper: would --resume skip this instance given these savepoint rows?"""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / f"{tmp_name}.jsonl"
        _write(p, rows)
        ids = _load_existing_ids(str(p))
    return _resume_key(iid, MODEL, THINK) in ids


def test_T17_one_no_patch_row_still_retries(tmp_path):
    """T17a: after ONE no-diff outcome we retry (attempt 2 is allowed)."""
    rows = [_row("django__django-1")]
    assert _no_patch_attempts(rows) == 1
    assert _load_existing_ids_from_rows(rows, "django__django-1") is False, (
        "a single no-diff outcome must still be retried once"
    )


def test_T17_two_no_patch_rows_stop_retrying(tmp_path):
    """T17b: the retry is bounded to ONE. A second no-diff outcome is final."""
    rows = [
        _row("django__django-1"),
        _row("django__django-1"),
    ]
    assert _no_patch_attempts(rows) == 2
    assert _load_existing_ids_from_rows(rows, "django__django-1") is True, (
        "option D: after two no-diff outcomes the instance is accepted as final"
    )


def test_T17_two_distinct_instances_do_not_share_an_attempt_budget(tmp_path):
    """T17c: the count is per instance -- mistake would let one instance's retry
    consume another's."""
    p = tmp_path / "direct.jsonl"
    _write(p, [
        _row("django__django-1"),
        _row("django__django-1"),
        _row("django__django-2"),   # only ONE attempt so far
    ])
    ids = _load_existing_ids(str(p))
    assert _resume_key("django__django-1", MODEL, THINK) in ids
    assert _resume_key("django__django-2", MODEL, THINK) not in ids, (
        "instance 2 must still get its own retry"
    )


# --------------------------------------------------------------------------
# T5 / T19 -- regressions: the cases that already work must not change.
# --------------------------------------------------------------------------

def test_T5_valid_row_with_patch_is_finished():
    """T5: a successful run is skipped, as before."""
    rows = [_row("django__django-1", patch_status="VALID",
                 model_patch="diff --git a/x b/x\n")]
    assert _no_patch_attempts(rows) == 0
    assert _load_existing_ids_from_rows(rows, "django__django-1") is True


def test_T19_mixed_rows_with_one_success_is_skipped():
    """T19 (finding I): 134 combinations in real data carry BOTH a successful and
    a failed/no-patch row. Order decides the outcome; one success must win."""
    rows = [
        _row("django__django-1"),                                   # no diff, attempt 1
        _row("django__django-1", patch_status="VALID",
             model_patch="diff --git a/x b/x\n"),                    # then it worked
    ]
    assert _load_existing_ids_from_rows(rows, "django__django-1") is True


def test_T19b_success_first_then_no_patch_is_also_skipped():
    """T19b: the reverse order too -- resume must not un-skip a finished run."""
    rows = [
        _row("django__django-1", patch_status="VALID",
             model_patch="diff --git a/x b/x\n"),
        _row("django__django-1"),
    ]
    assert _load_existing_ids_from_rows(rows, "django__django-1") is True
