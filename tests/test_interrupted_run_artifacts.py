"""An interrupted run must leave the same artefacts a completed one does.

Context: the exports (CSV, statistics, report, manifest) all live at the END of
run_experiments, so Ctrl-C used to discard the accounting for every run that had
already finished -- the results survived only in the jsonl savepoints, which do
not record tokens, cost or timing.

Measured with a real Ctrl-C mid-sweep: ONLY the CSV appeared.
manifest.json, generation_statistics.json and generation_report.md were missing.

That is the same failure class as the experiment.yaml bug (config written after
the run finished, so a crash lost it). It also matters because the manifest is a
citable record: a reader reconstructing what ran should not have to infer the
missing part from the savepoints.

The interrupt must still PROPAGATE -- swallowing it would make the sweep look
like it finished normally.
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
    """Succeeds until it hits ``die_on``, then raises KeyboardInterrupt."""

    def __init__(self, calls: list[str], die_on: str):
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
        result = ExperimentResult(
            instance_id=issue.instance_id, strategy="direct", model="test-model",
            execution=ExecutionResult(run=run),
            cost=CostCalculator().aggregate([inf]),
            evaluation=EvaluationResult(success=True, error=""),
        )
        return Patch(response="diff --git a/x b/x"), result


def _issue(i: int) -> Issue:
    return Issue(
        instance_id=f"django__django-{i}",
        repo="django/django",
        base_commit="0" * 40,
        problem_statement="p",
    )


def _interrupt(tmp_path, issues, die_on):
    calls: list[str] = []
    with pytest.raises(KeyboardInterrupt):
        run_experiments(
            issues,
            {"direct": _InterruptOn(calls, die_on)},
            base_dir=str(tmp_path),
            provider_name="test-provider",
            model="test-model",
            rate_limit_seconds=0,
        )
    exp_id = sorted(p.name for p in tmp_path.glob("EXP-*"))[-1]
    return tmp_path / exp_id, calls


def test_interrupt_still_propagates(tmp_path):
    """The interrupt must stop the run, not be swallowed by the export step."""
    calls = []
    with pytest.raises(KeyboardInterrupt):
        run_experiments(
            [_issue(1), _issue(2), _issue(3)],
            {"direct": _InterruptOn(calls, "django__django-2")},
            base_dir=str(tmp_path), provider_name="test", model="test-model",
            rate_limit_seconds=0,
        )
    assert calls == ["django__django-1", "django__django-2"], (
        "the run must stop at the interrupt, not continue to issue 3"
    )


@pytest.mark.parametrize("artifact", [
    "generation_result.csv",
    "manifest.json",
    "generation_statistics.json",
    "generation_report.md",
])
def test_interrupt_writes_the_artifact(tmp_path, artifact):
    """Every artefact a completed run produces must also exist after Ctrl-C.

    Parametrised so a failure names WHICH artefact regressed: before the fix only
    the CSV existed, and a single combined assertion would have hidden that the
    other three were missing for a different reason.
    """
    exp_dir, _ = _interrupt(tmp_path, [_issue(1), _issue(2), _issue(3), _issue(4)],
                            "django__django-3")
    path = exp_dir / artifact
    assert path.exists(), f"{artifact} missing after the interrupt"
    assert path.stat().st_size > 0, f"{artifact} is empty after the interrupt"


def test_interrupted_run_keeps_the_finished_runs_accounting(tmp_path):
    """The runs that DID finish must keep their token counts, not be reduced to
    what the savepoints hold (which has no tokens/cost/timing at all)."""
    import pandas as pd

    exp_dir, _ = _interrupt(tmp_path, [_issue(1), _issue(2), _issue(3)], "django__django-3")
    df = pd.read_csv(exp_dir / "generation_result.csv")
    df.columns = [str(c).lstrip("[") for c in df.columns]

    done = df[df["instance_id"] == "django__django-1"]
    assert len(done) == 1
    assert done.iloc[0]["total_tokens"] == 12, (
        "a completed run's token accounting was lost by the interrupt"
    )
    interrupted = df[df["instance_id"] == "django__django-3"]
    assert len(interrupted) == 1
    assert interrupted.iloc[0]["patch_status"] == "INTERRUPTED"


def test_interrupted_manifest_lists_what_ran(tmp_path):
    """The manifest is the shipping receipt: it must name the runs that happened."""
    exp_dir, _ = _interrupt(tmp_path, [_issue(1), _issue(2), _issue(3)], "django__django-3")
    manifest = json.loads((exp_dir / "manifest.json").read_text(encoding="utf-8"))

    ids = {r.get("instance_id") for r in manifest.get("results", [])}
    assert "django__django-1" in ids, (
        "a run that completed before the interrupt is absent from the manifest"
    )
    assert manifest.get("provider", {}).get("name") == "test-provider", (
        "the manifest must carry the real provider, not an empty placeholder"
    )
