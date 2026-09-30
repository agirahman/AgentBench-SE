import json
import os
import random
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

import pandas as pd

from models.issue import Issue
from models.result import (
    ExperimentResult,
    ExecutionResult,
    CostSummary,
    EvaluationResult,
)
from models.patch import Patch
from models.inference import InferenceRun, InferenceResult
from experiments.csv_exporter import flatten_for_csv
from experiments.swebench_adapter import (
    collect_test_files,
    extract_diff,
    strip_test_files,
    validate_applicability,
)
from agents.tools import ensure_repo_root
from evaluation.statistics import export_statistics_json, generate_summary_md
from evaluation.cost import PricingTable
from evaluation.retry import is_provider_error, is_rate_limit_error
from config import Config
from experiment_id import generate_experiment_id, create_experiment_dir
from experiments.observability import build_experiment_manifest, write_issue_run_summary
from utils.logger import logger


class StrategyProtocol(Protocol):
    def run(self, issue: Issue) -> tuple[Patch, ExperimentResult]: ...


def _save_artifacts(
    output_dir: str,
    instance_id: str,
    strategy_name: str,
    inferences,
    final_patch: str,
    messages=None,
) -> None:
    """Simpan artifact per-agent ke ``<exp_dir>/artifacts/<instance_id>/<strategy_name>/``.

    Dipisah per strategi agar ``messages.jsonl``, ``patch.txt``, dan ``<role>.md``
    setiap strategi tidak saling menimpa.
    """
    art_dir = Path(f"{output_dir}/artifacts/{instance_id}/{strategy_name}")
    art_dir.mkdir(parents=True, exist_ok=True)

    for inf in inferences:
        if not inf.role:
            continue
        (art_dir / f"{inf.role}.md").write_text(inf.response, encoding="utf-8")
        # Persist the model's reasoning channel separately (thinking mode).
        # Critical for diagnosing premature-stop failures where content holds
        # only a preamble while the actual reasoning lives here.
        reasoning = getattr(inf, "reasoning_content", "") or ""
        if reasoning.strip():
            (art_dir / f"{inf.role}_reasoning.md").write_text(reasoning, encoding="utf-8")

    (art_dir / "patch.txt").write_text(final_patch, encoding="utf-8")

    if messages:
        with (art_dir / "messages.jsonl").open("w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")

    # Per-agent tool-call log: which tool, how many times, by which agent.
    # Each inference carries tool_calls (empty for non-tool agents).
    tool_lines = []
    for inf in inferences:
        if not inf.role:
            continue
        for call in getattr(inf, "tool_calls", []) or []:
            tool_lines.append(
                {
                    "agent": inf.role,
                    "tool": call.get("name"),
                    "arguments": call.get("arguments"),
                    "result_preview": (call.get("result") or "")[:2000],
                }
            )
    if tool_lines:
        with (art_dir / "tool_calls.jsonl").open("w", encoding="utf-8") as f:
            for line in tool_lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _resume_key(instance_id: str, model: str, thinking: bool) -> str:
    """Composite key so resume is safe across configs (model/thinking).

    Item 8c: a bare ``instance_id`` match would wrongly skip an issue when the
    SAME experiment folder is reused with a different model or thinking flag.
    """
    return f"{instance_id}|{model}|{thinking}"


#: A row counts as "done" only if the run actually produced a patch. An errored
#: row is appended with the same resume keys as a success so that a later
#: --resume can RETRY it; treating it as done would make --resume silently
#: preserve the failures it exists to recover from.
#:
#: RATE_LIMIT and PROVIDER_ERROR were added when the error path stopped stamping
#: every exception "TIMEOUT" (runner.py:493-498), but this set was not updated --
#: so those two statuses fell outside it. Today ``_is_finished_entry`` still
#: rejects them via the empty patch, but only by accident of the error path
#: writing ``model_patch: ""``. If that ever changed, a rate-limited run would be
#: counted as finished and skipped by every later --resume. The list is the
#: explicit statement of intent, so it has to name them.
_PATCH_STATUS_FAILED = {
    "TIMEOUT",
    "ERROR",
    "FAILED",
    "RATE_LIMIT",
    "PROVIDER_ERROR",
}


def _is_finished_entry(entry: dict) -> bool:
    """Did this jsonl row represent a completed run?

    A row is finished when it carries a non-empty patch. The error path writes
    ``model_patch: ""`` together with ``patch_status: "TIMEOUT"``
    (runner.py:452-465), so an empty patch is the reliable signal that the
    instance still needs to run.
    """
    status = str(entry.get("patch_status") or "").upper()
    if status in _PATCH_STATUS_FAILED:
        return False
    if entry.get("error_type"):
        return False
    return bool((entry.get("model_patch") or "").strip())


def _load_existing_ids(jsonl_path: str) -> set[str]:
    """Baca file jsonl yang sudah ada, return set composite resume keys.

    Each key is ``instance_id|model|thinking`` (see ``_resume_key``).

    Only FINISHED runs are returned. A run that died (provider 502, rate limit,
    git failure) leaves a row with an empty patch and ``patch_status: TIMEOUT``;
    counting it as done would skip the instance on every later --resume, so the
    failure would never be retried and would stay in the results as if it were a
    real outcome. Measured on EXP-20260929-022 django-11019/review, a 502.
    """
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
                iid = entry.get("instance_id")
                if iid is None:
                    continue
                model = entry.get("model_name_or_path", "")
                thinking = entry.get("thinking", False)
                if not _is_finished_entry(entry):
                    continue
                ids.add(_resume_key(iid, model, thinking))
            except json.JSONDecodeError:
                continue
    return ids


def _append_jsonl(jsonl_path: str, entry: dict) -> None:
    """Append 1 baris ke jsonl (savepoint)."""
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_jsonl_entries(jsonl_path: str) -> list[dict]:
    """Read every parseable row from a savepoint, in order."""
    entries: list[dict] = []
    if not os.path.exists(jsonl_path):
        return entries
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _merge_csv_rows(csv_path: str, new_rows: list[dict]) -> list[dict]:
    """Merge this session's rows with the rows already on disk.

    Why this exists: the CSV was written from ``all_results``, which only ever
    holds runs started in THIS process, and ``to_csv`` overwrites. So a ``--resume``
    after a crash rewrote ``generation_result.csv`` with only the post-interruption
    runs and silently dropped everything before it -- while the jsonl savepoints
    stayed complete. Measured independently by two auditors: a 3-issue first pass
    followed by a 5-issue resume left the CSV with 2 rows out of 5.

    That is the failure a long sweep cannot tolerate. 50 issues x 3 strategies is
    ~6 hours and a mid-run interruption is likely, so the recovery path must
    preserve the paid-for work instead of erasing it.

    Merging at the CSV level (rather than rebuilding from the jsonl savepoints)
    keeps every recorded column: the savepoints carry only the prediction contract,
    so a rebuild would report token and cost columns as zero for earlier runs --
    turning a data-loss bug into a silent data-quality bug.

    Rows are keyed by ``(instance_id, strategy)`` and the NEWEST wins, so a retry
    after a failure replaces the failed row rather than appearing twice. That
    deduplication is also what keeps the evaluation wrapper from counting one
    instance twice (a duplicate row made ``resolved/total`` exceed 100%).
    """
    merged: dict[tuple[str, str], dict] = {}

    if os.path.exists(csv_path):
        try:
            old = pd.read_csv(csv_path)
            # The header is written with a leading "[" (a pandas artifact of the
            # original writer), so strip it before matching column names.
            old.columns = [str(c).lstrip("[") for c in old.columns]
            for record in old.to_dict(orient="records"):
                key = (str(record.get("instance_id")), str(record.get("strategy")))
                merged[key] = record
        except Exception as exc:  # noqa: BLE001 - never lose the new rows over this
            logger.warning(
                f"Could not read the existing CSV at {csv_path} for merging "
                f"({type(exc).__name__}: {exc}); writing this session's rows only."
            )

    for record in new_rows:
        key = (str(record.get("instance_id")), str(record.get("strategy")))
        merged[key] = record

    return list(merged.values())


def _results_from_flat_rows(rows: list[dict]) -> list[ExperimentResult]:
    """Rebuild manifest inputs from flattened CSV rows.

    The manifest is built from ``ExperimentResult`` objects, but after a merge the
    rows for earlier runs exist only as CSV records. Token and timing properties on
    ``ExperimentResult`` are computed from ``run.inferences``, so the recorded
    totals are carried by one synthetic ``InferenceResult`` -- that keeps
    ``total_tokens``, ``execution_time`` and the cost columns faithful to what was
    originally measured instead of collapsing them to zero.

    Only fields the manifest actually reads are reconstructed; nothing is invented
    for values the CSV does not carry.
    """
    out: list[ExperimentResult] = []

    def _num(value, default=0.0) -> float:
        try:
            if value is None or (isinstance(value, float) and pd.isna(value)):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    for row in rows:
        patch_preview = str(row.get("patch_preview") or "")
        synth = InferenceResult(
            role="rebuilt",
            response="",
            usage={
                "prompt_tokens": int(_num(row.get("input_tokens_total"))),
                "completion_tokens": int(_num(row.get("output_tokens"))),
                "total_tokens": int(_num(row.get("total_tokens"))),
                "cached_tokens": int(_num(row.get("input_tokens_cached"))),
            },
            execution_time=_num(row.get("execution_time")),
            api_turns=int(_num(row.get("total_turns"), 1)) or 1,
        )
        run = InferenceRun(patch=patch_preview, inferences=[synth])
        cost = CostSummary(
            input_cost_usd=_num(row.get("input_cost_usd_total")),
            output_cost_usd=_num(row.get("output_cost_usd")),
            total_cost_usd=_num(row.get("cost_usd_offpeak")),
            total_cost_idr=_num(row.get("cost_idr_offpeak")),
            pricing_version=str(row.get("pricing_version") or ""),
            cached_input_tokens=int(_num(row.get("input_tokens_cached"))),
            regular_input_tokens=int(_num(row.get("input_tokens_regular"))),
            cached_input_cost_usd=_num(row.get("input_cost_usd_cached")),
            regular_input_cost_usd=_num(row.get("input_cost_usd_regular")),
            peak_total_cost_usd=_num(row.get("cost_usd_peak_total")),
            peak_total_cost_idr=_num(row.get("cost_idr_peak_total")),
            actual_cost_usd=_num(row.get("cost_usd_actual")),
            actual_cost_idr=_num(row.get("cost_idr_actual")),
        )
        result = ExperimentResult(
            instance_id=str(row.get("instance_id") or ""),
            strategy=str(row.get("strategy") or ""),
            model=str(row.get("model") or ""),
            execution=ExecutionResult(run=run),
            cost=cost,
            evaluation=EvaluationResult(
                success=bool(row.get("generated")),
                error=str(row.get("error") or "") if not pd.isna(row.get("error")) else "",
                timestamp=str(row.get("timestamp") or ""),
            ),
            difficulty=str(row.get("difficulty") or ""),
            patch_status=str(row.get("patch_status") or ""),
            apply_status=str(row.get("apply_status") or ""),
        )
        out.append(result)
    return out


def run_experiments(
    issues: list[Issue],
    strategies: dict[str, StrategyProtocol],
    base_dir: str = "results",
    provider_name: str = "unknown",
    rate_limit_seconds: float = 1.5,
    resume: bool = False,
    agents: list[dict[str, str]] | None = None,
    model: str = "",
    on_experiment_start: Callable[[str, str], None] | None = None,
    experiment_id: str | None = None,
) -> tuple[pd.DataFrame, str]:
    """Execute experiments and dump results to a per-experiment folder.

    Args:
        issues: List of Issue objects to process
        strategies: Dict of strategy_name -> strategy_object
        base_dir: Root results directory (default: results/)
        provider_name: Name of provider (gemini/groq/deepseek)
        rate_limit_seconds: Delay between strategies (for API rate limiting)
        resume: If True, skip issues already completed in existing jsonl files
        model: Actual configured model id (e.g. cmd/deepseek/deepseek-v4-flash).
            Used for resume keys and error rows so records never carry a
            provider name in place of a model id.
        on_experiment_start: Called with (exp_dir, exp_id) as soon as the
            directory exists, before the first run. The caller uses it to write
            the configuration record up front. Without it the config was only
            written AFTER every run finished, so a crash or a kill left a
            directory full of patches with no record of the settings that
            produced them -- and a multi-hour sweep is exactly where that
            matters. Failures here are logged, never fatal: losing a metadata
            write must not abort an experiment that is already running.
        experiment_id: Continue an EXISTING experiment instead of creating a new
            one. This is what makes ``resume`` functional: the directory is
            chosen first and the resume scan then reads the savepoints inside
            it. Previously every invocation minted a fresh id, so the scan ran
            against an empty directory and could never skip anything -- the flag
            was accepted and inert. Selecting the directory is separate from
            ``resume``: passing an id without ``resume`` deliberately re-runs
            everything into that directory (e.g. re-measuring after a code
            change), which would otherwise be impossible.

    Returns:
        (DataFrame, experiment_id) where DataFrame is the flattened results.csv
    """
    effective_model = model or provider_name
    exp_id = experiment_id or generate_experiment_id()
    exp_dir = create_experiment_dir(base_dir, exp_id)
    if on_experiment_start is not None:
        try:
            on_experiment_start(exp_dir, exp_id)
        except Exception as exc:  # noqa: BLE001 - metadata must not kill a run
            logger.warning(
                f"Could not write the early config record for {exp_id}: "
                f"{type(exc).__name__}: {exc}"
            )
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
    # Consecutive rate-limit failures. When the provider's usage window is
    # exhausted, every further call fails the same way — continuing only wastes
    # the remaining quota and produces a run full of 429 rows. Trip a breaker
    # and stop cleanly so --resume can finish later.
    consecutive_rate_limits = 0
    rate_limit_stopped = False

    for issue in issues:
        for name, strategy in strategies.items():
            done += 1

            # Resume skip — composite key (instance_id|model|thinking) so a
            # different config in the same experiment folder is not wrongly skipped.
            expected_key = _resume_key(
                issue.instance_id, effective_model, Config.DEEPSEEK_THINKING
            )
            if expected_key in done_ids[name]:
                skipped += 1
                logger.info(f"[{done}/{total}] SKIP (resume) {name} on {issue.instance_id}")
                continue

            logger.info(
                f"[{done}/{total}] Running {name} on {issue.instance_id} ({issue.difficulty})..."
            )
            logger.info(f"  → Issue loaded: {len(issue.problem_statement)} chars")
            t0 = time.time()
            try:
                logger.info(f"  → Strategy initialized: {name}")
                logger.info(f"  → API call to {provider_name}...")
                patch, result = strategy.run(issue)
                result.difficulty = issue.difficulty
                result.thinking = Config.DEEPSEEK_THINKING
                result.max_tokens = Config.MAX_TOKENS
                elapsed = time.time() - t0
                result.evaluation.timestamp = datetime.now(timezone.utc).isoformat()

                # --- Truncated JSON protection ---
                last_finish = ""
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
                # A successful call means the provider is healthy again; the
                # breaker only counts *consecutive* failures.
                consecutive_rate_limits = 0
                # Semantic check: patch_status only proves the diff arithmetic is
                # well formed. apply_status records whether the patch can really
                # be applied to the target repo, so a "valid" patch that merely
                # guessed its lines is not mistaken for a working one.
                if Config.APPLY_CHECK_ENABLED and diff.strip():
                    try:
                        result.apply_status = validate_applicability(
                            diff, ensure_repo_root(issue.repo, issue.base_commit)
                        )
                    except Exception as exc:  # noqa: BLE001 - never fail a run
                        logger.warning(
                            f"  ⚠ apply check failed for {issue.instance_id} "
                            f"({name}): {type(exc).__name__}: {exc}"
                        )
                        result.apply_status = "UNKNOWN"
                all_results.append(result)

                if last_finish == "length":
                    logger.warning(
                        f"  ⚠ Response truncated (finish_reason='length') for {issue.instance_id} ({name}) "
                        f"— patch_status={patch_status}, patch_len={len(diff)}, "
                        f"response_len={len(patch.response)}"
                    )

                # --- Savepoint append: per-strategy .jsonl ---
                # Last-resort fallback (Item 8a): if extract_diff failed but the
                # raw response still contains a unified diff, ship the raw text
                # so a valid patch is not silently dropped as "no report".
                model_patch = diff if diff.strip() else ""
                if not model_patch and "diff --git" in patch.response:
                    logger.warning(
                        f"  ⚠ extract_diff failed but raw response has a diff for "
                        f"{issue.instance_id} ({name}) — using raw response as patch"
                    )
                    model_patch = patch.response

                # Keep test files out of the submitted patch. The harness resets
                # test files and applies its own gold test patch; a model patch
                # that creates the same test file makes `git checkout <base_commit>
                # <path>` fail ("did not match any file(s) known to git"), and the
                # gold patch then fails with "already exists in working directory".
                # Evaluation continues regardless (the eval script has no `set -e`),
                # so the wrong tests could run. The harness supplies its own tests.
                if model_patch.strip() and getattr(issue, "test_patch", ""):
                    gold_test_files = collect_test_files(issue.test_patch)
                    if gold_test_files:
                        model_patch, stripped = strip_test_files(model_patch, gold_test_files)
                        if stripped:
                            logger.info(
                                f"  ✂ Removed {len(stripped)} test file(s) from patch "
                                f"for {issue.instance_id} ({name}): {', '.join(stripped)}"
                            )
                            if not model_patch.strip():
                                logger.warning(
                                    f"  ⚠ Patch for {issue.instance_id} ({name}) became "
                                    "empty after removing test files — the model only "
                                    "edited tests, so it will not resolve."
                                )

                # Re-derive status from the patch we ACTUALLY submit. Both checks
                # above ran on the raw captured diff, before test-file stripping,
                # so their verdicts could describe a patch that is never sent.
                # Observed on EXP-20260927-008 (django-10914 review): the raw diff
                # was HUNK_MISMATCH/NORMALIZE because the agent's edit to a gold
                # test file left that hunk one line short, while the submitted
                # patch (that hunk removed) was clean and applied with rc=0.
                # Reporting the raw verdict would have libelled a good patch.
                if model_patch.strip() and model_patch != diff:
                    patch_status = extract_diff(model_patch).status
                    result.patch_status = patch_status
                    if Config.APPLY_CHECK_ENABLED:
                        try:
                            result.apply_status = validate_applicability(
                                model_patch, ensure_repo_root(issue.repo, issue.base_commit)
                            )
                        except Exception as exc:  # noqa: BLE001 - never fail a run
                            logger.warning(
                                f"  ⚠ apply check failed for {issue.instance_id} "
                                f"({name}): {type(exc).__name__}: {exc}"
                            )
                            result.apply_status = "UNKNOWN"

                pred_entry = {
                    "instance_id": issue.instance_id,
                    "model_patch": model_patch,
                    "model_name_or_path": result.model,
                    "strategy": name,
                    "patch_status": patch_status,
                    "thinking": result.thinking,
                    "max_tokens": result.max_tokens,
                }
                strategy_jsonl = str(pred_dir / f"{name}.jsonl")
                _append_jsonl(strategy_jsonl, pred_entry)
                all_predictions.append(pred_entry)

                # --- Savepoint append: aggregated predictions.jsonl ---
                agg_jsonl = str(pred_dir / "predictions.jsonl")
                _append_jsonl(agg_jsonl, pred_entry)

                if not model_patch.strip():
                    logger.warning(
                        f"  ⚠ Patch empty/invalid for {issue.instance_id} ({name}) — "
                        f"recorded as empty ({patch_status})"
                    )
                else:
                    # Persist exactly what is sent to the Modal evaluator,
                    # not the raw (possibly JSON-wrapped) response.
                    Path(f"{exp_dir}/patches/{issue.instance_id}_{name}.txt").write_text(
                        model_patch, encoding="utf-8"
                    )
                    logger.info(f"  → Patch saved: {issue.instance_id}_{name}.txt")

                # Always persist artifacts (messages, per-role responses,
                # tool_calls) — even when the patch is empty/invalid — so the
                # tool-call trail stays available for debugging and analysis.
                _save_artifacts(
                    str(exp_dir),
                    issue.instance_id,
                    name,
                    result.execution.inferences,
                    patch.response,
                    result.execution.run.messages,
                )

                logger.success(
                    f"  ✅ {elapsed:.1f}s | {result.execution.total_tokens} tokens "
                    f"({result.execution.prompt_tokens} in + {result.execution.completion_tokens} out) | "
                    f"{result.execution.inference_count} inferences | "
                    f"${result.cost.total_cost_usd:.6f} | model={result.model}"
                )

                write_issue_run_summary(
                    output_dir=str(exp_dir),
                    issue=issue,
                    strategy_name=name,
                    patch_status=patch_status,
                    elapsed_seconds=elapsed,
                    total_tokens=result.execution.total_tokens,
                    success=bool(diff.strip()),
                    error="",
                )

                if rate_limit_seconds > 0:
                    # Honour the caller's --rate-limit instead of a fixed 5-10s:
                    # the parameter was previously accepted and then ignored.
                    jitter = random.uniform(0.0, min(2.0, rate_limit_seconds * 0.5))
                    delay = rate_limit_seconds + jitter
                    logger.info(f"Rate limit delay: {delay:.1f}s")
                    time.sleep(delay)

            except Exception as e:
                error_detail = str(e)
                elapsed_err = time.time() - t0
                logger.error(
                    f"  ❌ FAILED: {issue.instance_id} ({name}, {issue.difficulty}) — {type(e).__name__}: {error_detail[:400]}"
                )

                # Rate-limit breaker: a 429 on one instance means the rest will
                # fail too. Count consecutive hits and stop the run cleanly.
                if is_rate_limit_error(e):
                    consecutive_rate_limits += 1
                    limit = Config.RATE_LIMIT_CONSECUTIVE_LIMIT
                    logger.warning(
                        f"  ⏳ Rate limit hit ({consecutive_rate_limits}"
                        f"{f'/{limit}' if limit > 0 else ''} consecutive)"
                    )
                    if limit > 0 and consecutive_rate_limits >= limit:
                        rate_limit_stopped = True
                        logger.error(
                            f"Stopping run after {consecutive_rate_limits} consecutive "
                            f"rate-limit failures — the provider usage window is "
                            f"exhausted. Re-run with --resume once it resets."
                        )
                else:
                    consecutive_rate_limits = 0

                # Label the failure by CAUSE, not by a single catch-all. Every
                # unhandled exception used to be stamped "TIMEOUT", so a provider
                # 502, an HTTP 429 and a git error were indistinguishable in the
                # results and all read as time-outs downstream. Measured:
                # EXP-20260929-022 django-11019/review recorded a 502
                # (ENOTFOUND opencode.ai) as TIMEOUT, and EXP-20260824-005
                # recorded 11 consecutive 429s plus a git failure the same way.
                # The distinction matters because only some of these are worth
                # retrying, and a run that died at the provider must not be read
                # as a strategy that ran out of time.
                if is_rate_limit_error(e):
                    failure_status = "RATE_LIMIT"
                elif is_provider_error(e):
                    failure_status = "PROVIDER_ERROR"
                else:
                    failure_status = "ERROR"

                error_entry = {
                    "instance_id": issue.instance_id,
                    "model_patch": "",
                    "model_name_or_path": effective_model,
                    "strategy": name,
                    "patch_status": failure_status,
                    # Keep resume keys symmetric with success rows; otherwise
                    # --resume under thinking mode re-runs every errored instance.
                    "thinking": Config.DEEPSEEK_THINKING,
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
                    model=effective_model,
                    difficulty=issue.difficulty,
                    execution=empty_exec,
                    cost=empty_cost,
                    evaluation=empty_eval,
                    patch_status=failure_status,
                ))

                # Persist an issue-level summary even on failure so manifest
                # artifact references never dangle.
                write_issue_run_summary(
                    output_dir=str(exp_dir),
                    issue=issue,
                    strategy_name=name,
                    patch_status=failure_status,
                    elapsed_seconds=elapsed_err,
                    total_tokens=0,
                    success=False,
                    error=f"{type(e).__name__}: {error_detail[:400]}",
                )

            if rate_limit_stopped:
                break
        if rate_limit_stopped:
            break

    # --- Final exports ---
    # "generation_" prefix disambiguates phase-1 outputs from the eval-phase
    # files that report_generator writes under eval/ (results.csv, statistics.json).
    #
    # MERGE with whatever is already on disk before writing. all_results holds only
    # the runs started in this process, so overwriting here dropped every earlier
    # row when --resume was used -- see _merge_csv_rows. A fresh run has no old CSV
    # and merges to exactly its own rows, so this is a no-op in the normal case.
    csv_path = f"{exp_dir}/generation_result.csv"
    new_rows = [flatten_for_csv(r) for r in all_results]
    merged_rows = _merge_csv_rows(csv_path, new_rows)
    df = pd.DataFrame(merged_rows)
    df.to_csv(csv_path, index=False)
    logger.success(f"CSV exported: {csv_path} ({len(df)} rows)")

    if len(df) > len(new_rows):
        logger.info(
            f"  (merged {len(df) - len(new_rows)} row(s) recorded by an earlier "
            f"session in this experiment directory)"
        )

    # The manifest and the pre-eval report are built from ExperimentResult objects.
    # After a merge, earlier runs exist only as CSV rows, so rebuild them from the
    # merged frame: otherwise the manifest would claim fewer issues processed than
    # the CSV contains -- the "shipping receipt" disagreeing with the shipment.
    manifest_results = _results_from_flat_rows(merged_rows)

    stats_path = f"{exp_dir}/generation_statistics.json"
    model_name = df["model"].iloc[0] if len(df) else provider_name
    pricing = PricingTable.get(model_name) if model_name else None
    export_statistics_json(df, stats_path, pricing=pricing, usd_idr_rate=Config.USD_IDR_RATE)
    logger.success(f"Statistics exported: {stats_path}")

    # NOTE: this is the PRE-EVAL generation report (patch generation only),
    # NOT the resolved rate. The authoritative resolved rate is eval/summary.md
    # produced by report_generator from Modal results. See PLAN.md Item 5.
    summary_path = f"{exp_dir}/generation_report.md"
    generate_summary_md(df, summary_path, pricing=pricing, usd_idr_rate=Config.USD_IDR_RATE)
    logger.success(f"Generation report (PRE-EVAL) exported: {summary_path}")

    manifest = build_experiment_manifest(
        issues=issues,
        strategies=list(strategies.keys()),
        provider_name=provider_name,
        experiment_id=exp_id,
        output_dir=str(exp_dir),
        results=manifest_results,
        agents=agents,
    )
    manifest_path = f"{exp_dir}/manifest.json"
    Path(manifest_path).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.success(f"Experiment manifest exported: {manifest_path}")

    # Log per-strategy counts
    for strat_name in strategies:
        strat_jsonl = str(pred_dir / f"{strat_name}.jsonl")
        count = sum(1 for _ in open(strat_jsonl, "r", encoding="utf-8") if _.strip())
        logger.success(f"Per-strategy predictions: {strat_jsonl} ({count} entries)")

    # --- Completeness check ---
    # Nothing used to compare the number of finished runs against the number
    # planned, so a sweep that silently lost runs still reported success. At 50
    # issues x 3 strategies a missing instance is easy to miss by eye and
    # expensive to discover after the analysis is written.
    expected_total = len(issues) * len(strategies)
    finished_total = 0
    missing: list[str] = []
    for strat_name in strategies:
        strat_jsonl = str(pred_dir / f"{strat_name}.jsonl")
        finished_here = {
            entry.get("instance_id")
            for entry in _read_jsonl_entries(strat_jsonl)
            if _is_finished_entry(entry)
        }
        finished_total += len(finished_here)
        for issue in issues:
            if issue.instance_id not in finished_here:
                missing.append(f"{strat_name}:{issue.instance_id}")

    if finished_total == expected_total:
        logger.success(
            f"Completeness: {finished_total}/{expected_total} finished runs recorded."
        )
    else:
        logger.error(
            f"Completeness: {finished_total}/{expected_total} finished runs recorded "
            f"-- {len(missing)} run(s) did NOT complete."
        )
        for entry in missing[:20]:
            logger.error(f"    incomplete: {entry}")
        if len(missing) > 20:
            logger.error(f"    ... and {len(missing) - 20} more")
        logger.error(
            "  Re-run with --resume to finish the incomplete runs before analysing."
        )
        # Persist the shortfall so a downstream reader cannot mistake this for a
        # complete sweep just because the process exited 0.
        completeness = {
            "expected": expected_total,
            "finished": finished_total,
            "missing": missing,
            "complete": False,
        }
        try:
            Path(f"{exp_dir}/INCOMPLETE.json").write_text(
                json.dumps(completeness, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning(f"Could not write INCOMPLETE.json: {exc}")

    if skipped:
        logger.info(f"Resume: skipped {skipped} already-completed entries")

    if rate_limit_stopped:
        logger.warning("")
        logger.warning("!" * 60)
        logger.warning(
            "  RUN STOPPED EARLY — provider rate limit / usage window exhausted."
        )
        logger.warning(
            f"  Processed {done - 1}/{total} planned runs before stopping."
        )
        logger.warning(
            "  Data collected so far is valid and already flushed to disk."
        )
        logger.warning(
            "  Re-run the same command with --resume once the limit resets."
        )
        logger.warning("!" * 60)

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
