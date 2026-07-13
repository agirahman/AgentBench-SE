import json
import time
from pathlib import Path
from dataclasses import asdict

import pandas as pd

from models.issue import Issue
from models.result import ExperimentResult
from experiments.swebench_adapter import extract_diff
from utils.logger import logger


def run_experiments(
    issues: list[Issue],
    strategies: dict[str, object],
    output_dir: str = "results",
) -> pd.DataFrame:
    all_results: list[ExperimentResult] = []
    all_predictions: list[dict] = []
    Path(f"{output_dir}/patches").mkdir(parents=True, exist_ok=True)
    Path(f"{output_dir}/csv").mkdir(parents=True, exist_ok=True)
    Path(f"{output_dir}/predictions").mkdir(parents=True, exist_ok=True)

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
                all_results.append(result)

                diff = extract_diff(patch.response)
                all_predictions.append({
                    "instance_id": issue.instance_id,
                    "model_patch": diff,
                })

                Path(f"{output_dir}/patches/{issue.instance_id}_{name}.txt").write_text(
                    patch.response, encoding="utf-8"
                )
                logger.success(f"  OK ({elapsed:.1f}s, {result.total_tokens} tokens)")
            except Exception as e:
                logger.error(f"  FAILED: {e}")
                all_results.append(ExperimentResult(
                    instance_id=issue.instance_id,
                    strategy=name,
                    execution_time=0,
                    inference_count=0,
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    patch_preview="",
                    error=str(e),
                ))

    df = pd.DataFrame([asdict(r) for r in all_results])
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
