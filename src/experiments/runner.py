import json
import time
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from models.issue import Issue
from models.result import ExperimentResult
from models.inference import InferenceRun
from experiments.swebench_adapter import extract_diff
from evaluation.cost import CostCalculator
from utils.logger import logger


def _save_artifacts(
    output_dir: str,
    instance_id: str,
    run: InferenceRun,
    final_patch: str,
) -> None:
    """Simpan artifact per-agent ke results/artifacts/<instance_id>/."""
    art_dir = Path(f"{output_dir}/artifacts/{instance_id}")
    art_dir.mkdir(parents=True, exist_ok=True)

    for inf in run.inferences:
        if inf.role == "planner":
            (art_dir / "planner.md").write_text(inf.response, encoding="utf-8")
        elif inf.role == "executor":
            (art_dir / "executor.md").write_text(inf.response, encoding="utf-8")
        elif inf.role == "reviewer":
            (art_dir / "reviewer.md").write_text(inf.response, encoding="utf-8")

    (art_dir / "patch.txt").write_text(final_patch, encoding="utf-8")


def run_experiments(
    issues: list[Issue],
    strategies: dict[str, object],
    output_dir: str = "results",
    provider_name: str = "unknown",
    rate_limit_seconds: float = 1.5,
) -> pd.DataFrame:
    all_results: list[ExperimentResult] = []
    all_predictions: list[dict] = []
    Path(f"{output_dir}/patches").mkdir(parents=True, exist_ok=True)
    Path(f"{output_dir}/csv").mkdir(parents=True, exist_ok=True)
    Path(f"{output_dir}/predictions").mkdir(parents=True, exist_ok=True)
    Path(f"{output_dir}/artifacts").mkdir(parents=True, exist_ok=True)

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
                result.execution_time = elapsed
                result.model = provider_name
                # Hitung biaya per inference lalu agregasi ke ExperimentResult.
                # Cost TIDAK disimpan di InferenceResult (raw SDK metadata),
                # murni hasil perhitungan CostCalculator.
                calculator = CostCalculator()
                for inf in result.run.inferences:
                    cost = calculator.calculate(inf)
                    result.input_cost_usd += cost.input_cost_usd
                    result.output_cost_usd += cost.output_cost_usd
                    result.cost_usd += cost.total_cost_usd
                    result.cost_idr += cost.total_cost_idr

                all_results.append(result)

                diff = extract_diff(patch.response)
                all_predictions.append({
                    "instance_id": issue.instance_id,
                    "model_patch": diff,
                })

                # Simpan patch final (format lama — preview 1 file per strategi)
                Path(f"{output_dir}/patches/{issue.instance_id}_{name}.txt").write_text(
                    patch.response, encoding="utf-8"
                )

                # Simpan artifact per-agent (Kritik #6, #7)
                _save_artifacts(output_dir, issue.instance_id, result.run, patch.response)

                logger.success(
                    f"  OK ({elapsed:.1f}s, {result.total_tokens} tokens, "
                    f"{result.inference_count} inferences)"
                )

                if rate_limit_seconds > 0:
                    time.sleep(rate_limit_seconds)

            except Exception as e:
                logger.error(f"  FAILED: {e}")
                empty_run = InferenceRun(patch="", inferences=[])
                all_results.append(ExperimentResult(
                    instance_id=issue.instance_id,
                    strategy=name,
                    model=provider_name,
                    run=empty_run,
                    execution_time=0.0,
                    inference_count=0,
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    patch_preview="",
                    error=str(e),
                ))

    # Ekspor CSV — exclude nested ``run`` field (artefak disimpan terpisah di artifacts/)
    rows = [{k: v for k, v in asdict(r).items() if k != "run"} for r in all_results]
    df = pd.DataFrame(rows)
    csv_path = f"{output_dir}/csv/experiment_results.csv"
    df.to_csv(csv_path, index=False)
    logger.success(f"CSV exported: {csv_path}")

    jsonl_path = f"{output_dir}/predictions/predictions.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for pred in all_predictions:
            f.write(json.dumps(pred) + "\n")
    logger.success(
        f"Predictions exported: {jsonl_path} ({len(all_predictions)} entries)"
    )

    return df
