import json
from pathlib import Path

from experiments.observability import build_experiment_manifest, write_issue_run_summary
from models.issue import Issue
from models.inference import InferenceRun
from models.result import (
    ExperimentResult,
    ExecutionResult,
    CostSummary,
    EvaluationResult,
)


def _make_result(instance_id, strategy, patch, status="VALID"):
    run = InferenceRun(patch=patch, inferences=[])
    exec_res = ExecutionResult(run=run)
    cost = CostSummary(0.001, 0.002, 0.003, 53.5, "test")
    eval_res = EvaluationResult(success=True, error="")
    return ExperimentResult(
        instance_id=instance_id,
        strategy=strategy,
        model="test-model",
        execution=exec_res,
        cost=cost,
        evaluation=eval_res,
        difficulty="hard",
        patch_status=status,
    )


def test_build_experiment_manifest_reports_dataset_mix(tmp_path):
    issues = [
        Issue(
            instance_id="django__django-1",
            repo="django/django",
            base_commit="abc",
            problem_statement="problem",
        ),
        Issue(
            instance_id="django__django-2",
            repo="django/django",
            base_commit="abc",
            problem_statement="problem",
        ),
        Issue(
            instance_id="requests-1",
            repo="psf/requests",
            base_commit="abc",
            problem_statement="problem",
        ),
    ]

    results = [
        _make_result("django__django-1", "direct", "--- a/x\n+++ b/x\n+print(1)"),
        _make_result("django__django-2", "direct", "", status="PARSE_ERROR"),
        _make_result("requests-1", "planning", "", status="TIMEOUT"),
    ]

    manifest = build_experiment_manifest(
        issues=issues,
        strategies=["direct", "planning"],
        provider_name="openrouter",
        experiment_id="EXP-20260722-001",
        output_dir=str(tmp_path),
        results=results,
    )

    assert manifest["dataset"]["n_issues"] == 3
    assert manifest["dataset"]["difficulty_counts"]["hard"] == 2
    assert manifest["dataset"]["difficulty_counts"]["easy"] == 1
    assert manifest["dataset"]["repo_counts"]["django/django"] == 2
    assert manifest["strategies"] == ["direct", "planning"]
    assert manifest["provider"]["name"] == "openrouter"
    assert manifest["provider"]["model"] == "test-model"
    assert manifest["execution_timestamp"]
    assert manifest["summary"]["total_issues_processed"] == 3
    assert manifest["summary"]["patch_generated_count"] == 1
    assert manifest["summary"]["empty_patch_count"] == 1
    assert manifest["summary"]["timeout_count"] == 1
    assert manifest["results"][0]["status"] == "PATCH_GENERATED"
    assert manifest["results"][0]["artifacts"]["generated_patch_path"] == (
        "patches/django__django-1_direct.txt"
    )
    assert manifest["results"][0]["tokens"]["cached_input"] == 0
    assert manifest["results"][0]["tokens"]["regular_input"] == 0
    assert manifest["results"][0]["cost"]["total_usd_off_peak"] == 0.003
    assert manifest["results"][0]["cost"]["total_usd_peak"] == 0.0
    assert manifest["results"][1]["status"] == "EMPTY_PATCH"
    assert manifest["results"][2]["status"] == "TIMEOUT"


def test_manifest_records_cached_tokens_and_peak_cost(tmp_path):
    run = InferenceRun(patch="--- a/x\n+++ b/x\n", inferences=[])
    exec_res = ExecutionResult(run=run)
    cost = CostSummary(
        0.001, 0.002, 0.003, 53.5, "test",
        cached_input_tokens=700,
        regular_input_tokens=300,
        cached_input_cost_usd=0.0001,
        regular_input_cost_usd=0.0009,
        peak_total_cost_usd=0.006,
        peak_total_cost_idr=107.0,
    )
    result = ExperimentResult(
        instance_id="django__django-1",
        strategy="direct",
        model="deepseek-v4-flash",
        execution=exec_res,
        cost=cost,
        evaluation=EvaluationResult(success=True, error=""),
        difficulty="hard",
        patch_status="VALID",
    )
    issue = Issue(
        instance_id="django__django-1",
        repo="django/django",
        base_commit="abc",
        problem_statement="problem",
    )
    manifest = build_experiment_manifest(
        issues=[issue],
        strategies=["direct"],
        provider_name="deepseek",
        experiment_id="EXP-20260816-001",
        output_dir=str(tmp_path),
        results=[result],
    )
    entry = manifest["results"][0]
    assert entry["tokens"]["cached_input"] == 700
    assert entry["tokens"]["regular_input"] == 300
    assert entry["cost"]["total_usd_off_peak"] == 0.003
    assert entry["cost"]["total_usd_peak"] == 0.006


def test_write_issue_run_summary_creates_json_artifact(tmp_path):
    issue = Issue(
        instance_id="sympy-1",
        repo="sympy/sympy",
        base_commit="abc",
        problem_statement="problem",
    )

    summary_path = write_issue_run_summary(
        output_dir=str(tmp_path),
        issue=issue,
        strategy_name="review",
        patch_status="VALID",
        elapsed_seconds=12.5,
        total_tokens=2400,
        success=True,
        error="",
    )

    summary = Path(summary_path)
    assert summary.exists()
    assert summary.name == "summary.json"
    assert summary.parent.name == "review"
    payload = json.loads(summary.read_text(encoding="utf-8"))
    assert payload["instance_id"] == "sympy-1"
    assert payload["strategy"] == "review"
    assert payload["difficulty"] == "hard"
    assert payload["patch_status"] == "VALID"
    assert payload["success"] is True
