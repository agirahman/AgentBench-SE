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
    results, resolved, total, empty, errors = classify_summary(summary, preds, None)

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
    results, resolved, total, empty, errors = classify_summary(summary, preds, None)

    assert (resolved, total, empty, errors) == (1, 2, 1, 0)
    assert {r["instance_id"]: r["failure_reason"] for r in results}[
        "django__django-11019"
    ] == "EMPTY_PATCH"


def test_total_falls_back_to_predictions_when_summary_has_no_count():
    summary = {"resolved_ids": ["django__django-10914"], "error_ids": []}
    preds = _preds(("django__django-10914", "diff"), ("django__django-11001", "diff"))
    _results, resolved, total, _empty, _errors = classify_summary(summary, preds, None)
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
    _results, resolved, total, empty, errors = classify_summary(summary, preds, None)
    assert (resolved, total, empty, errors) == (2, 3, 0, 0)
    assert resolved / (total - empty - errors) == resolved / total
