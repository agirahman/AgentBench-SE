"""Empty patches must not be counted as strategy failures.

An empty patch means the run died before producing anything -- in
EXP-20260929-022 it was a provider 502 for django-11019/review. The official
SWE-bench harness excludes these from the resolved/unresolved split and reports
them in their own bucket (`reporting.py`: "Instances with empty patches"), and it
drops them from the container run entirely (`run_evaluation.py:458`).

Our wrapper originally fell through to the unresolved branch, so a dead run
silently counted as a wrong answer. These tests call the real classifier.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from eval_modal import classify_summary  # noqa: E402


def _preds(*specs):
    """specs: (instance_id, patch) pairs, as loaded from predictions.jsonl."""
    return [{"instance_id": iid, "model_patch": patch} for iid, patch in specs]


def _classify(summary, preds):
    """Unpack the classifier's dict result the way the tests want it."""
    c = classify_summary(summary, preds, None)
    return c["results"], c["resolved"], c["total"], c["empty"], c["errors"], c


def test_empty_patch_is_labelled_empty_not_unresolved():
    """The dead run is classified EMPTY_PATCH, and not as a wrong answer."""
    summary = {
        "submitted_instances": 3,
        "resolved_ids": ["django__django-10914", "django__django-11001"],
        "unresolved_ids": [],
        "empty_patch_ids": ["django__django-11019"],
        "error_ids": [],
    }
    preds = _preds(
        ("django__django-10914", "diff --git a b"),
        ("django__django-11001", "diff --git c d"),
        ("django__django-11019", ""),
    )
    results, resolved, total, empty, errors, _c = _classify(summary, preds)

    assert (resolved, total, empty, errors) == (2, 3, 1, 0)

    by_id = {r["instance_id"]: r for r in results}
    dead = by_id["django__django-11019"]
    assert dead["failure_reason"] == "EMPTY_PATCH"
    assert dead["resolved"] is False
    # A stale report.json from the no-op container run says applied=True; we must
    # not repeat that claim, because there was no patch to apply.
    assert dead["patch_applied"] is False


def test_empty_ids_are_re_derived_when_the_summary_omits_them():
    """Older summaries may lack the bucket; fall back to inspecting the patches."""
    summary = {
        "submitted_instances": 2,
        "resolved_ids": ["django__django-10914"],
        "error_ids": [],
    }
    preds = _preds(("django__django-10914", "diff"), ("django__django-11019", ""))
    results, resolved, total, empty, errors, _c = _classify(summary, preds)

    assert (resolved, total, empty, errors) == (1, 2, 1, 0)
    assert {r["instance_id"]: r["failure_reason"] for r in results}[
        "django__django-11019"
    ] == "EMPTY_PATCH"


def test_total_falls_back_to_predictions_when_summary_has_no_count():
    summary = {"resolved_ids": ["django__django-10914"], "error_ids": []}
    preds = _preds(("django__django-10914", "diff"), ("django__django-11001", "diff"))
    _results, resolved, total, _empty, _errors, _c = _classify(summary, preds)
    assert (resolved, total) == (1, 2)


def test_the_two_rates_disagree_exactly_by_the_dead_run():
    """Both rates are reported; they differ only by the run that died.

    Pinning the arithmetic keeps the honest reading available. The comparable
    headline is resolved/submitted (67%); resolved/graded (100%) describes only
    the runs that actually produced something and must never be the headline,
    or losing a data point to an outage would look like an improvement.
    """
    resolved, empty = 2, 1
    total = 3
    graded = total - empty

    assert round(resolved / total * 100, 1) == 66.7
    assert round(resolved / graded * 100, 1) == 100.0
    assert resolved / graded > resolved / total, (
        "a lost data point must never be presented as the headline rate"
    )


def test_no_empty_patches_leaves_both_rates_identical():
    """The regression that matters for the other levels: nothing changes."""
    summary = {
        "submitted_instances": 3,
        "resolved_ids": ["django__django-10914", "django__django-11001"],
        "unresolved_ids": ["django__django-11019"],
        "empty_patch_ids": [],
        "error_ids": [],
    }
    preds = _preds(
        ("django__django-10914", "diff a"),
        ("django__django-11001", "diff b"),
        ("django__django-11019", "diff c"),
    )
    _results, resolved, total, empty, errors, _c = _classify(summary, preds)
    assert (resolved, total, empty, errors) == (2, 3, 0, 0)
    assert resolved / (total - empty - errors) == resolved / total


def test_a_retried_instance_is_not_counted_twice():
    """A duplicate savepoint row must not produce a rate above 100%.

    A retry after a failure appends a second row for the same instance. The
    harness deduplicates before evaluating (it builds a dict), but the classifier
    iterated the raw list: a retried instance incremented BOTH the numerator and
    the number of rows, while the denominator came from the harness's deduplicated
    count. Two populations, one ratio -- measured at 150% for a single duplicate.

    This is the headline number of the thesis, so the failure mode is a rate above
    100% appearing in a results file.
    """
    summary = {
        "submitted_instances": 2,
        "resolved_ids": ["django__django-10914", "django__django-11019"],
        "unresolved_ids": [],
        "empty_patch_ids": [],
        "error_ids": [],
    }
    # 11019 appears twice: first a dead attempt, then the retry that resolved it.
    preds = _preds(
        ("django__django-10914", "diff a"),
        ("django__django-11019", ""),
        ("django__django-11019", "diff b"),
    )
    results, resolved, total, empty, errors, _c = _classify(summary, preds)

    assert total == 2, "the denominator must be the harness's deduplicated count"
    assert resolved == 2
    assert resolved <= total, "resolved can never exceed the graded total"
    assert resolved / total <= 1.0
    assert len(results) == 2, f"one row per instance expected, got {len(results)}"
    ids = [r["instance_id"] for r in results]
    assert len(ids) == len(set(ids)), f"instance appears more than once: {ids}"


def test_the_last_row_wins_for_a_retried_instance():
    """The newer attempt is the one to report, matching the runner's CSV merge."""
    summary = {
        "submitted_instances": 1,
        "resolved_ids": ["django__django-11019"],
        "unresolved_ids": [],
        "empty_patch_ids": [],
        "error_ids": [],
    }
    preds = _preds(
        ("django__django-11019", ""),          # first attempt: dead
        ("django__django-11019", "diff good"),  # retry: the real answer
    )
    results, resolved, total, _empty, _errors, _c = _classify(summary, preds)

    assert (resolved, total) == (1, 1)
    assert len(results) == 1
    # The dead first attempt must not mark the instance as an empty patch.
    assert results[0]["failure_reason"] != "EMPTY_PATCH"


def test_classifier_flags_an_impossible_rate_instead_of_raising():
    """A guard, not a fix: if the arithmetic breaks again, do not emit a rate.

    A rate above 100% is nonsense that a reader might still quote, so the rate is
    withheld -- written as null in the results file.

    It must NOT raise: by the time classification runs, Modal has already executed
    and been paid for, so an exception would throw away a completed evaluation over
    a bookkeeping mismatch. The per-instance rows are still correct; only the ratio
    is in doubt, so only the ratio is refused.
    """
    summary = {
        "submitted_instances": 1,
        "resolved_ids": ["a", "b"],  # harness claims 2 resolved but submitted 1
        "unresolved_ids": [],
        "empty_patch_ids": [],
        "error_ids": [],
    }
    preds = _preds(("a", "diff a"), ("b", "diff b"))

    results, resolved, total, _empty, _errors, c = _classify(summary, preds)

    # It still returns usable rows rather than aborting.
    assert len(results) == 2
    assert (resolved, total) == (2, 1)
    # And it marks the ratio as unusable.
    assert c["rate_is_valid"] is False


def test_a_normal_run_is_marked_valid():
    """The flag must not fire on healthy input, or it will be ignored."""
    summary = {
        "submitted_instances": 2,
        "resolved_ids": ["a"],
        "unresolved_ids": ["b"],
        "empty_patch_ids": [],
        "error_ids": [],
    }
    _results, _resolved, _total, _empty, _errors, c = _classify(
        summary, _preds(("a", "diff a"), ("b", "diff b"))
    )
    assert c["rate_is_valid"] is True
