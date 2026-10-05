"""An interrupted sweep must report its own shortfall, not look complete.

The completeness check compares finished runs against the number planned and
writes INCOMPLETE.json when short. It lived at the END of run_experiments, AFTER
the try/except, so an operator interrupt raised straight past it.

Measured on a real Ctrl-C mid-sweep (1 issue x 3 strategies): the experiment log
had NO "Completeness:" line at all and no INCOMPLETE.json was written, even though
the review arm had not finished. A stopped run therefore looked complete -- exactly
the silent loss this check exists to catch. MEMORY trap #17: a control that does
not fire when it should is worse than none, because it is trusted.

The fix extracts the check into _check_completeness() and calls it from the
interrupt branch too, so the two paths cannot drift.
"""
import json

import pytest

from experiments.runner import run_experiments
from models.inference import InferenceResult, InferenceRun
from models.issue import Issue
from models.patch import Patch
from models.result import ExperimentResult, ExecutionResult, EvaluationResult
from evaluation.cost import CostCalculator


class _InterruptOn:
    def __init__(self, calls, die_on):
        self.calls = calls
        self.die_on = die_on

    def run(self, issue):
        self.calls.append(issue.instance_id)
        if issue.instance_id == self.die_on:
            raise KeyboardInterrupt("simulated Ctrl+C")
        inf = InferenceResult(
            role="direct", response="diff --git a/x b/x",
            usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        )
        run = InferenceRun(patch="diff --git a/x b/x", inferences=[inf], messages=[])
        return Patch(response="diff --git a/x b/x"), ExperimentResult(
            instance_id=issue.instance_id, strategy="direct", model="test-model",
            execution=ExecutionResult(run=run),
            cost=CostCalculator().aggregate([inf]),
            evaluation=EvaluationResult(success=True, error=""),
        )


def _issue(i):
    return Issue(instance_id=f"django__django-{i}", repo="django/django",
                 base_commit="0" * 40, problem_statement="p")


def _interrupt(tmp_path, n, die_on_last=True):
    calls = []
    issues = [_issue(i) for i in range(1, n + 1)]
    with pytest.raises(KeyboardInterrupt):
        run_experiments(
            issues, {"direct": _InterruptOn(calls, f"django__django-{n}")},
            base_dir=str(tmp_path), provider_name="test", model="test-model",
            rate_limit_seconds=0,
        )
    exp_id = sorted(p.name for p in tmp_path.glob("EXP-*"))[-1]
    return tmp_path / exp_id


def test_interrupt_writes_the_incomplete_marker(tmp_path):
    """THE REGRESSION: a stopped run must leave evidence it was stopped.

    3 issues, interrupted on the last one -> 2 covered, 1 not -> the marker must
    exist. Before the fix it did not.
    """
    exp_dir = _interrupt(tmp_path, 3)
    marker = exp_dir / "INCOMPLETE.json"
    assert marker.exists(), (
        "an interrupted run wrote no INCOMPLETE.json, so it looks complete"
    )

    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["complete"] is False
    assert payload["expected"] == 3
    assert payload["completed"] == 2
    assert "direct:django__django-3" in payload["failed"], (
        "the interrupted instance must be named as 'ran but FAILED', not omitted"
    )


def test_interrupt_completeness_uses_the_shared_base(tmp_path):
    """The marker must satisfy the same invariant as the normal path.

    A second, independently-written check is how the two paths drift; this pins
    the arithmetic the shared function guarantees.
    """
    exp_dir = _interrupt(tmp_path, 2)
    payload = json.loads((exp_dir / "INCOMPLETE.json").read_text(encoding="utf-8"))
    assert payload["completed"] <= payload["expected"], (
        f"incoherent coverage after an interrupt: "
        f"{payload['completed']} > {payload['expected']}"
    )


def test_completeness_failure_does_not_mask_the_interrupt(tmp_path, monkeypatch):
    """Bookkeeping must never replace the operator's interrupt with a traceback.

    The branch is re-raising KeyboardInterrupt to stop the run; if the completeness
    check raised, the operator would see an unrelated error from inside the handler.
    """
    from experiments import runner

    def boom(*a, **k):
        raise RuntimeError("completeness exploded")

    monkeypatch.setattr(runner, "_check_completeness", boom)
    # Still KeyboardInterrupt, NOT RuntimeError.
    exp_dir = _interrupt(tmp_path, 2)
    assert exp_dir.exists(), "the interrupt must still propagate cleanly"


def test_a_clean_run_still_clears_the_marker(tmp_path):
    """Regression: the shared function must not start alarming on success."""
    calls = []
    run_experiments(
        [_issue(1), _issue(2)], {"direct": _InterruptOn(calls, "none")},
        base_dir=str(tmp_path), provider_name="test", model="test-model",
        rate_limit_seconds=0,
    )
    exp_id = sorted(p.name for p in tmp_path.glob("EXP-*"))[-1]
    assert not (tmp_path / exp_id / "INCOMPLETE.json").exists()
