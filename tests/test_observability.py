import json
from pathlib import Path

from experiments.observability import build_experiment_manifest, write_issue_run_summary
from models.issue import Issue


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

    manifest = build_experiment_manifest(
        issues=issues,
        strategies=["direct", "planning"],
        provider_name="openrouter",
        experiment_id="EXP-20260722-001",
        output_dir=str(tmp_path),
    )

    assert manifest["dataset"]["n_issues"] == 3
    assert manifest["dataset"]["difficulty_counts"]["hard"] == 2
    assert manifest["dataset"]["difficulty_counts"]["easy"] == 1
    assert manifest["dataset"]["repo_counts"]["django/django"] == 2
    assert manifest["strategies"] == ["direct", "planning"]
    assert manifest["provider"]["name"] == "openrouter"


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

    assert Path(summary_path).exists()
    payload = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    assert payload["instance_id"] == "sympy-1"
    assert payload["strategy"] == "review"
    assert payload["difficulty"] == "hard"
    assert payload["patch_status"] == "VALID"
    assert payload["success"] is True
