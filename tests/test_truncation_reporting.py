"""Truncation must be recorded in the artefact, not inferred from logs.

EXP-20260928-003 reported "direct 8/10, planning 8/10, review 6/10" with no
indication that 6 of the 30 runs had been cut off by their turn cap -- and that
5 of those 6 failed. Those outcomes measure the granted budget, not the
strategy, so the numbers were cleaner-looking than the data justified.

SWE-bench's own convention is to report such runs under their own heading and
keep them in the denominator ("the failure lines ... never remove anything from
the total", docs/guides/evaluation.md). That requires the information to exist in
the export, which is what these tests pin down.
"""
from models.inference import InferenceResult, InferenceRun
from models.result import ExperimentResult, ExecutionResult, EvaluationResult
from experiments.csv_exporter import flatten_for_csv
from evaluation.cost import CostCalculator


def _inference(role="executor", truncated=False):
    return InferenceResult(role=role, response="patch", truncated=truncated)


def _result(inferences):
    run = InferenceRun(patch="diff", inferences=inferences, messages=[])
    return ExperimentResult(
        instance_id="django__django-11001",
        strategy="review",
        model="demo",
        execution=ExecutionResult(run=run),
        cost=CostCalculator().aggregate(inferences),
        evaluation=EvaluationResult(success=True, error=""),
    )


def test_inference_result_defaults_to_not_truncated():
    """A normal act must not be flagged: the flag has to mean something."""
    assert InferenceResult(role="planner", response="x").truncated is False


def test_export_records_a_clean_run_as_not_truncated():
    row = flatten_for_csv(_result([_inference(), _inference("reviewer")]))
    assert row["truncated"] is False
    assert row["truncated_acts"] == 0


def test_export_flags_a_run_with_one_truncated_act():
    row = flatten_for_csv(_result([_inference(truncated=True), _inference("reviewer")]))
    assert row["truncated"] is True
    assert row["truncated_acts"] == 1


def test_export_counts_every_truncated_act():
    """The COUNT matters: it says how much of the run was cut off."""
    row = flatten_for_csv(
        _result(
            [
                _inference("planner", truncated=True),
                _inference(truncated=True),
                _inference("reviewer"),
            ]
        )
    )
    assert row["truncated_acts"] == 2
    assert row["truncated"] is True


def test_truncation_is_independent_of_success():
    """A truncated run can still succeed -- so it cannot be inferred from the grade.

    Measured: django-10914/review hit its cap and resolved anyway. If truncation
    were derived from "unresolved", that run would be misreported as clean.
    """
    row = flatten_for_csv(_result([_inference(truncated=True)]))
    assert row["generated"] is True
    assert row["truncated"] is True
