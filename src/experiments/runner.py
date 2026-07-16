import json
import time
from datetime import datetime
from pathlib import Path
from typing import Protocol

import pandas as pd

from models.issue import Issue
from models.result import (
    ExperimentResult,
    ExecutionResult,
    CostSummary,
    EvaluationResult,
)
from models.patch import Patch
from models.inference import InferenceRun
from experiments.csv_exporter import flatten_for_csv
from experiments.swebench_adapter import extract_diff
from evaluation.statistics import export_statistics_json, generate_summary_md
from experiment_id import generate_experiment_id, create_experiment_dir
from utils.logger import logger


class StrategyProtocol(Protocol):
    def run(self, issue: Issue) -> tuple[Patch, ExperimentResult]: ...


def _save_artifacts(
    output_dir: str,
    instance_id: str,
    inferences,
    final_patch: str,
) -> None:
    """Simpan artifact per-agent ke ``<exp_dir>/artifacts/<instance_id>/``."""
    art_dir = Path(f"{output_dir}/artifacts/{instance_id}")
    art_dir.mkdir(parents=True, exist_ok=True)

    for inf in inferences:
        if inf.role == "planner":
            (art_dir / "planner.md").write_text(inf.response, encoding="utf-8")
        elif inf.role == "executor":
            (art_dir / "executor.md").write_text(inf.response, encoding="utf-8")
        elif inf.role == "reviewer":
            (art_dir / "reviewer.md").write_text(inf.response, encoding="utf-8")

    (art_dir / "patch.txt").write_text(final_patch, encoding="utf-8")


def run_experiments(
    issues: list[Issue],
    strategies: dict[str, StrategyProtocol],
    base_dir: str = "results",
    provider_name: str = "unknown",
    rate_limit_seconds: float = 1.5,
) -> tuple[pd.DataFrame, str]:
    """Execute experiments and dump results to a per-experiment folder.

    Args:
        issues: List of Issue objects to process
        strategies: Dict of strategy_name -> strategy_object
        base_dir: Root results directory (default: results/)
        provider_name: Name of provider (gemini/groq)
        rate_limit_seconds: Delay between strategies (for API rate limiting)

    Returns:
        (DataFrame, experiment_id) where DataFrame is the flattened results.csv
    """
    exp_id = generate_experiment_id()
    exp_dir = create_experiment_dir(base_dir, exp_id)
    Path(f"{exp_dir}/patches").mkdir(parents=True, exist_ok=True)
    Path(f"{exp_dir}/predictions").mkdir(parents=True, exist_ok=True)

    all_results: list[ExperimentResult] = []
    all_predictions: list[dict] = []

    total = len(issues) * len(strategies)
    done = 0

    for issue in issues:
        for name, strategy in strategies.items():
            done += 1
            logger.info(f"[{done}/{total}] Running {name} on {issue.instance_id}...")
            try:
                t0 = time.time()
                patch, result = strategy.run(issue)
                elapsed = time.time() - t0
                result.evaluation.timestamp = datetime.utcnow().isoformat()
                all_results.append(result)

                diff = extract_diff(patch.response)
                all_predictions.append({
                    "instance_id": issue.instance_id,
                    "model_patch": diff,
                })

                Path(f"{exp_dir}/patches/{issue.instance_id}_{name}.txt").write_text(
                    patch.response, encoding="utf-8"
                )
                _save_artifacts(
                    str(exp_dir), issue.instance_id, result.execution.inferences, patch.response
                )

                logger.success(
                    f"  OK ({elapsed:.1f}s, {result.execution.total_tokens} tokens, "
                    f"{result.execution.inference_count} inferences, "
                    f"${result.cost.total_cost_usd:.6f})"
                )

                if rate_limit_seconds > 0:
                    time.sleep(rate_limit_seconds)

            except Exception as e:
                logger.error(f"  FAILED: {e}")
                empty_run = InferenceRun(patch="", inferences=[])
                empty_exec = ExecutionResult(run=empty_run)
                empty_cost = CostSummary(0.0, 0.0, 0.0, 0.0, "")
                empty_eval = EvaluationResult(success=False, error=str(e))
                all_results.append(ExperimentResult(
                    instance_id=issue.instance_id,
                    strategy=name,
                    model=provider_name,
                    execution=empty_exec,
                    cost=empty_cost,
                    evaluation=empty_eval,
                ))

    rows = [flatten_for_csv(r) for r in all_results]
    df = pd.DataFrame(rows)
    csv_path = f"{exp_dir}/results.csv"
    df.to_csv(csv_path, index=False)
    logger.success(f"CSV exported: {csv_path}")

    stats_path = f"{exp_dir}/statistics.json"
    export_statistics_json(df, stats_path)
    logger.success(f"Statistics exported: {stats_path}")

    summary_path = f"{exp_dir}/summary.md"
    generate_summary_md(df, summary_path)
    logger.success(f"Summary markdown exported: {summary_path}")

    jsonl_path = f"{exp_dir}/predictions/predictions.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for pred in all_predictions:
            f.write(json.dumps(pred) + "\n")
    logger.success(
        f"Predictions exported: {jsonl_path} ({len(all_predictions)} entries)"
    )

    return df, exp_id
