import json
import os
import random
import time
from collections import Counter
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
from evaluation.cost import PricingTable
from config import Config
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


def _load_existing_ids(jsonl_path: str) -> set[str]:
    """Baca file jsonl yang sudah ada, return set instance_id yang sudah done."""
    ids = set()
    if not os.path.exists(jsonl_path):
        return ids
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if "instance_id" in entry:
                    ids.add(entry["instance_id"])
            except json.JSONDecodeError:
                continue
    return ids


def _append_jsonl(jsonl_path: str, entry: dict) -> None:
    """Append 1 baris ke jsonl (savepoint)."""
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_experiments(
    issues: list[Issue],
    strategies: dict[str, StrategyProtocol],
    base_dir: str = "results",
    provider_name: str = "unknown",
    rate_limit_seconds: float = 1.5,
    resume: bool = False,
) -> tuple[pd.DataFrame, str]:
    """Execute experiments and dump results to a per-experiment folder.

    Args:
        issues: List of Issue objects to process
        strategies: Dict of strategy_name -> strategy_object
        base_dir: Root results directory (default: results/)
        provider_name: Name of provider (gemini/groq/deepseek)
        rate_limit_seconds: Delay between strategies (for API rate limiting)
        resume: If True, skip issues already completed in existing jsonl files

    Returns:
        (DataFrame, experiment_id) where DataFrame is the flattened results.csv
    """
    exp_id = generate_experiment_id()
    exp_dir = create_experiment_dir(base_dir, exp_id)
    Path(f"{exp_dir}/patches").mkdir(parents=True, exist_ok=True)
    pred_dir = Path(f"{exp_dir}/predictions")
    pred_dir.mkdir(parents=True, exist_ok=True)

    # Per-experiment log sink
    exp_log_file = f"{exp_dir}/logs/experiment.log"
    _sink_id = logger.add(exp_log_file, level="INFO", rotation="10 MB")
    logger.info(f"Per-experiment log: {exp_log_file}")

    # --- Resume: collect done ids per strategy ---
    done_ids: dict[str, set[str]] = {}
    if resume:
        for strat_name in strategies:
            jsonl_path = str(pred_dir / f"{strat_name}.jsonl")
            done_ids[strat_name] = _load_existing_ids(jsonl_path)
            if done_ids[strat_name]:
                logger.info(f"Resume: {len(done_ids[strat_name])} already done in {strat_name}")
        logger.info(f"Resume mode enabled — skipping completed issues")
    else:
        for strat_name in strategies:
            done_ids[strat_name] = set()

    all_results: list[ExperimentResult] = []
    all_predictions: list[dict] = []

    total = len(issues) * len(strategies)
    done = 0
    skipped = 0

    for issue in issues:
        for name, strategy in strategies.items():
            done += 1

            # Resume skip1
            if issue.instance_id in done_ids[name]:
                skipped += 1
                logger.info(f"[{done}/{total}] SKIP (resume) {name} on {issue.instance_id}")
                continue

            logger.info(
                f"[{done}/{total}] Running {name} on {issue.instance_id} ({issue.difficulty})..."
            )
            logger.info(f"  → Issue loaded: {len(issue.problem_statement)} chars")
            try:
                t0 = time.time()
                logger.info(f"  → Strategy initialized: {name}")
                logger.info(f"  → API call to {provider_name}...")
                patch, result = strategy.run(issue)
                result.difficulty = issue.difficulty
                elapsed = time.time() - t0
                result.evaluation.timestamp = datetime.utcnow().isoformat()

                # --- Truncated JSON protection ---
                try:
                    last_finish = (
                        result.execution.inferences[-1].finish_reason
                        if result.execution.inferences else ""
                    )
                    patch_result = extract_diff(patch.response, finish_reason=last_finish)
                    diff = patch_result.patch
                    patch_status = patch_result.status
                except Exception:
                    diff = ""
                    patch_status = "PARSE_ERROR"

                result.patch_status = patch_status
                all_results.append(result)

                # --- Savepoint append: per-strategy .jsonl ---
                pred_entry = {
                    "instance_id": issue.instance_id,
                    "model_patch": diff if diff.strip() else "",
                    "model_name_or_path": result.model,
                    "strategy": name,
                    "patch_status": patch_status,
                }
                strategy_jsonl = str(pred_dir / f"{name}.jsonl")
                _append_jsonl(strategy_jsonl, pred_entry)
                all_predictions.append(pred_entry)

                # --- Savepoint append: aggregated predictions.jsonl ---
                agg_jsonl = str(pred_dir / "predictions.jsonl")
                _append_jsonl(agg_jsonl, pred_entry)

                if not diff.strip():
                    logger.warning(
                        f"  ⚠ Patch empty/invalid for {issue.instance_id} ({name}) — "
                        f"recorded as empty ({patch_status})"
                    )
                else:
                    Path(f"{exp_dir}/patches/{issue.instance_id}_{name}.txt").write_text(
                        patch.response, encoding="utf-8"
                    )
                    _save_artifacts(
                        str(exp_dir), issue.instance_id, result.execution.inferences, patch.response
                    )
                    logger.info(f"  → Patch saved: {issue.instance_id}_{name}.txt")

                logger.success(
                    f"  ✅ {elapsed:.1f}s | {result.execution.total_tokens} tokens "
                    f"({result.execution.prompt_tokens} in + {result.execution.completion_tokens} out) | "
                    f"{result.execution.inference_count} inferences | "
                    f"${result.cost.total_cost_usd:.6f} | model={result.model}"
                )

                if rate_limit_seconds > 0:
                    delay = random.uniform(5, 10)
                    logger.info(f"Rate limit delay: {delay:.1f}s")
                    time.sleep(delay)

            except Exception as e:
                error_detail = str(e)
                logger.error(
                    f"  ❌ FAILED: {issue.instance_id} ({name}, {issue.difficulty}) — {type(e).__name__}: {error_detail[:400]}"
                )

                error_entry = {
                    "instance_id": issue.instance_id,
                    "model_patch": "",
                    "model_name_or_path": provider_name,
                    "strategy": name,
                    "patch_status": "TIMEOUT",
                    "error_type": type(e).__name__,
                    "error_message": error_detail,
                }
                strategy_jsonl = str(pred_dir / f"{name}.jsonl")
                _append_jsonl(strategy_jsonl, error_entry)
                all_predictions.append(error_entry)

                agg_jsonl = str(pred_dir / "predictions.jsonl")
                _append_jsonl(agg_jsonl, error_entry)

                empty_run = InferenceRun(patch="", inferences=[])
                empty_exec = ExecutionResult(run=empty_run)
                empty_cost = CostSummary(0.0, 0.0, 0.0, 0.0, "")
                empty_eval = EvaluationResult(success=False, error=str(e))
                all_results.append(ExperimentResult(
                    instance_id=issue.instance_id,
                    strategy=name,
                    model=provider_name,
                    difficulty=issue.difficulty,
                    execution=empty_exec,
                    cost=empty_cost,
                    evaluation=empty_eval,
                    patch_status="TIMEOUT",
                ))

    # --- Final exports ---
    rows = [flatten_for_csv(r) for r in all_results]
    df = pd.DataFrame(rows)
    csv_path = f"{exp_dir}/results.csv"
    df.to_csv(csv_path, index=False)
    logger.success(f"CSV exported: {csv_path}")

    stats_path = f"{exp_dir}/statistics.json"
    model_name = df["model"].iloc[0] if len(df) else provider_name
    pricing = PricingTable.get(model_name) if model_name else None
    export_statistics_json(df, stats_path, pricing=pricing, usd_idr_rate=Config.USD_IDR_RATE)
    logger.success(f"Statistics exported: {stats_path}")

    summary_path = f"{exp_dir}/summary.md"
    generate_summary_md(df, summary_path, pricing=pricing, usd_idr_rate=Config.USD_IDR_RATE)
    logger.success(f"Summary markdown exported: {summary_path}")

    # Log per-strategy counts
    for strat_name in strategies:
        strat_jsonl = str(pred_dir / f"{strat_name}.jsonl")
        count = sum(1 for _ in open(strat_jsonl, "r", encoding="utf-8") if _.strip())
        logger.success(f"Per-strategy predictions: {strat_jsonl} ({count} entries)")

    if skipped:
        logger.info(f"Resume: skipped {skipped} already-completed entries")

    # --- Failure summary ---
    valid = sum(1 for p in all_predictions if p.get("patch_status") == "VALID")
    total_preds = len(all_predictions)
    failed = total_preds - valid
    from collections import Counter
    status_counts = Counter(p.get("patch_status", "UNKNOWN") for p in all_predictions)

    logger.info("")
    logger.info("=" * 60)
    logger.info(f"  Run Summary: {exp_id}")
    logger.info(f"  Total runs : {total_preds}")
    logger.info(f"  Valid      : {valid} ({valid/total_preds*100:.1f}%)" if total_preds else "  Valid: 0")
    logger.info(f"  Failed     : {failed} ({failed/total_preds*100:.1f}%)" if total_preds else "  Failed: 0")
    if failed:
        logger.info("  Breakdown:")
        for status_name in ("MALFORMED_HEADER", "PLACEHOLDER_ONLY", "OFFSET_TOO_LARGE",
                            "BAD_BODY", "HUNK_MISMATCH", "TRUNCATED", "PARSE_ERROR",
                            "NO_DIFF", "EMPTY", "TIMEOUT"):
            cnt = status_counts.get(status_name, 0)
            if cnt:
                logger.info(f"    {status_name}: {cnt}")
    norm = status_counts.get("NORMALIZE", 0)
    if norm:
        logger.info(f"  Normalized: {norm} (hunk header corrected, still valid)")
    logger.info("=" * 60)

    logger.remove(_sink_id)
    return df, exp_id
