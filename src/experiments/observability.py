import json
from pathlib import Path
from typing import Any

from models.issue import Issue


def build_experiment_manifest(
    *,
    issues: list[Issue],
    strategies: list[str],
    provider_name: str,
    experiment_id: str,
    output_dir: str,
) -> dict[str, Any]:
    """Build a structured manifest describing the experiment dataset and execution setup."""
    difficulty_counts = {"easy": 0, "medium": 0, "hard": 0, "unknown": 0}
    repo_counts: dict[str, int] = {}

    for issue in issues:
        difficulty = issue.difficulty
        difficulty_counts[difficulty] = difficulty_counts.get(difficulty, 0) + 1
        repo_counts[issue.repo] = repo_counts.get(issue.repo, 0) + 1

    manifest = {
        "experiment": {
            "id": experiment_id,
            "output_dir": output_dir,
        },
        "provider": {
            "name": provider_name,
        },
        "dataset": {
            "name": "princeton-nlp/SWE-bench_Lite",
            "n_issues": len(issues),
            "repo_counts": repo_counts,
            "difficulty_counts": {k: v for k, v in difficulty_counts.items() if v > 0},
        },
        "strategies": strategies,
    }
    return manifest


def write_issue_run_summary(
    *,
    output_dir: str,
    issue: Issue,
    strategy_name: str,
    patch_status: str,
    elapsed_seconds: float,
    total_tokens: int,
    success: bool,
    error: str = "",
) -> str:
    """Write a small JSON artifact summarizing an issue-level run."""
    artifact_dir = Path(output_dir) / "artifacts" / issue.instance_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    summary_path = artifact_dir / f"{strategy_name}_summary.json"

    payload = {
        "instance_id": issue.instance_id,
        "repo": issue.repo,
        "difficulty": issue.difficulty,
        "strategy": strategy_name,
        "patch_status": patch_status,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "total_tokens": total_tokens,
        "success": success,
        "error": error,
    }
    summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(summary_path)
