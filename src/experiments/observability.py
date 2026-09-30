import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import Config
from models.issue import Issue
from models.result import ExperimentResult


#: Failure statuses that are NOT "the model produced nothing" -- they mean the run
#: never got a fair chance. Kept separate so the manifest can distinguish an
#: infrastructure failure from a model that genuinely returned an empty patch.
_FAILURE_STATUSES = (
    "TIMEOUT",
    "RATE_LIMIT",
    "PROVIDER_ERROR",
    "ERROR",
    "FAILED",
)


def _result_status(result: ExperimentResult) -> str:
    """Map a run's outcome to one manifest bucket.

    Every failure status used to fall through to ``EMPTY_PATCH``, so a provider
    502, a rate limit and a model that really returned nothing were
    indistinguishable in the manifest -- and ``execution_status`` only looked at
    the ``TIMEOUT`` bucket, so a sweep that died entirely on rate limits was
    reported as ``COMPLETED``. Both hid exactly the failures a 150-run sweep needs
    to see.
    """
    status = str(result.patch_status or "").upper()
    if status in _FAILURE_STATUSES:
        return status
    if status in ("VALID", "NORMALIZE") and result.execution.patch.strip():
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

    status_counts = {"PATCH_GENERATED": 0, "EMPTY_PATCH": 0}
    for name in _FAILURE_STATUSES:
        status_counts[name] = 0
    for r in results:
        bucket = _result_status(r)
        status_counts[bucket] = status_counts.get(bucket, 0) + 1

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
                "patch_status": r.patch_status,
                "apply_status": r.apply_status,
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
            "timeout_count": status_counts.get("TIMEOUT", 0),
            # Every failure bucket, not just TIMEOUT. A sweep that died on rate
            # limits or provider errors used to report plain COMPLETED, because
            # only the TIMEOUT bucket was inspected.
            "failure_counts": {
                name: status_counts.get(name, 0)
                for name in _FAILURE_STATUSES
                if status_counts.get(name, 0)
            },
            # Honest status: a run with any failure is not plain COMPLETED.
            "execution_status": (
                "COMPLETED_WITH_ERRORS"
                if any(status_counts.get(name, 0) for name in _FAILURE_STATUSES)
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
