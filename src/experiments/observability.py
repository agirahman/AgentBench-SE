import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import Config
from models.issue import Issue
from models.result import ExperimentResult


def _result_status(result: ExperimentResult) -> str:
    if result.patch_status == "TIMEOUT":
        return "TIMEOUT"
    if result.patch_status in ("VALID", "NORMALIZE") and result.execution.patch.strip():
        return "PATCH_GENERATED"
    return "EMPTY_PATCH"


def build_experiment_manifest(
    *,
    issues: list[Issue],
    strategies: list[str],
    provider_name: str,
    experiment_id: str,
    output_dir: str,
    results: list[ExperimentResult],
    agents: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build a machine-readable execution manifest (shipping receipt).

    Mencatat metadata hasil eksekusi: ringkasan, hasil per-issue, dan lokasi
    artefak. Config input (blueprint) ada di ``experiment.yaml`` — tidak
    diduplikasi di sini.
    """
    difficulty_counts = {"easy": 0, "medium": 0, "hard": 0, "unknown": 0}
    repo_counts: dict[str, int] = {}

    for issue in issues:
        difficulty = issue.difficulty
        difficulty_counts[difficulty] = difficulty_counts.get(difficulty, 0) + 1
        repo_counts[issue.repo] = repo_counts.get(issue.repo, 0) + 1

    status_counts = {"PATCH_GENERATED": 0, "EMPTY_PATCH": 0, "TIMEOUT": 0}
    for r in results:
        status_counts[_result_status(r)] += 1

    model = results[0].model if results else provider_name

    api_requests_by_strategy: dict[str, int] = {}
    result_entries = []
    for r in results:
        status = _result_status(r)
        api_turns = sum(
            getattr(inf, "api_turns", 1) for inf in r.execution.inferences
        )
        api_requests_by_strategy[r.strategy] = (
            api_requests_by_strategy.get(r.strategy, 0) + api_turns
        )
        result_entries.append(
            {
                "instance_id": r.instance_id,
                "strategy": r.strategy,
                "model": r.model,
                "status": status,
                "difficulty": r.difficulty,
                "api_turns": api_turns,
                "tokens": {
                    "prompt": r.execution.prompt_tokens,
                    "cached_input": r.cost.cached_input_tokens,
                    "regular_input": r.cost.regular_input_tokens,
                    "completion": r.execution.completion_tokens,
                    "total": r.execution.total_tokens,
                },
                "execution_time_seconds": round(r.execution.execution_time, 3),
                "cost": {
                    "total_usd": round(r.cost.total_cost_usd, 6),
                    "total_idr": round(r.cost.total_cost_idr, 2),
                    "total_usd_off_peak": round(r.cost.total_cost_usd, 6),
                    "total_usd_peak": round(r.cost.peak_total_cost_usd, 6),
                },
                "artifacts": {
                    "generated_patch_path": (
                        f"patches/{r.instance_id}_{r.strategy}.txt"
                        if status == "PATCH_GENERATED"
                        else ""
                    ),
                    "summary_path": (
                        f"artifacts/{r.instance_id}/{r.strategy}/summary.json"
                    ),
                },
            }
        )

    manifest = {
        "experiment_id": experiment_id,
        "execution_timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": {
            "name": provider_name,
            "model": model,
        },
        "dataset": {
            "name": "SWE-bench/SWE-bench_Lite",
            "n_issues": len(issues),
            "repo_counts": repo_counts,
            "difficulty_counts": {k: v for k, v in difficulty_counts.items() if v > 0},
        },
        "strategies": strategies,
        "agents": agents or [],
        "summary": {
            "total_issues_processed": len(results),
            "patch_generated_count": status_counts["PATCH_GENERATED"],
            "empty_patch_count": status_counts["EMPTY_PATCH"],
            "timeout_count": status_counts["TIMEOUT"],
            # Honest status: a run with timeouts/errors is not plain COMPLETED.
            "execution_status": (
                "COMPLETED_WITH_ERRORS"
                if status_counts["TIMEOUT"] > 0
                else "COMPLETED"
            ),
            "api_requests_by_strategy": api_requests_by_strategy,
        },
        "results": result_entries,
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
    """Write a small JSON artifact summarizing an issue-level run.

    Disimpan ke ``<exp_dir>/artifacts/<instance_id>/<strategy_name>/summary.json``
    agar seragam dengan struktur artifact per-agent.
    """
    artifact_dir = Path(output_dir) / "artifacts" / issue.instance_id / strategy_name
    artifact_dir.mkdir(parents=True, exist_ok=True)
    summary_path = artifact_dir / "summary.json"

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
