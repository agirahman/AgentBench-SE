from models.result import ExperimentResult


def flatten_for_csv(result: ExperimentResult) -> dict:
    """Flatten nested ExperimentResult → flat dict for CSV export."""
    # Real API-turn count: each agent invocation may span multiple HTTP turns
    # when tool-calling is active (default 1 per inference for single-shot).
    inferences = result.execution.inferences
    total_turns = sum(getattr(inf, "api_turns", 1) for inf in inferences) if inferences else 0
    return {
        "instance_id": result.instance_id,
        "strategy": result.strategy,
        "model": result.model,
        "difficulty": result.difficulty,
        "inference_count": result.execution.inference_count,
        "total_turns": total_turns,
        "execution_time": result.execution.execution_time,
        "prompt_tokens": result.execution.prompt_tokens,
        "cached_input_tokens": result.cost.cached_input_tokens,
        "regular_input_tokens": result.cost.regular_input_tokens,
        "completion_tokens": result.execution.completion_tokens,
        "total_tokens": result.execution.total_tokens,
        "patch_preview": result.execution.patch_preview,
        "input_cost_usd": result.cost.input_cost_usd,
        "output_cost_usd": result.cost.output_cost_usd,
        "cost_usd": result.cost.total_cost_usd,
        "cost_idr": result.cost.total_cost_idr,
        "actual_cost_usd": result.cost.actual_cost_usd,
        "actual_cost_idr": result.cost.actual_cost_idr,
        "cached_input_cost_usd": result.cost.cached_input_cost_usd,
        "regular_input_cost_usd": result.cost.regular_input_cost_usd,
        "peak_total_cost_usd": result.cost.peak_total_cost_usd,
        "peak_total_cost_idr": result.cost.peak_total_cost_idr,
        "pricing_version": result.cost.pricing_version,
        "generated": result.evaluation.success,
        "error": result.evaluation.error,
        "patch_status": result.patch_status,
        "raw_regex_fixed": result.patch_status == "NORMALIZE",
        "hunk_mismatch_resolved": result.patch_status == "NORMALIZE",
        "timestamp": result.evaluation.timestamp,
        "timestamp_wib": _to_wib(result.evaluation.timestamp),
        "window": _window_of(result),
    }


def _to_wib(ts_utc: str) -> str:
    """Convert a UTC ISO timestamp to WIB (UTC+7) ISO string, or '' if invalid."""
    from datetime import datetime, timezone, timedelta
    try:
        dt = datetime.fromisoformat(ts_utc)
    except (ValueError, TypeError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt + timedelta(hours=7)).isoformat()


def _window_of(result) -> str:
    """Window (peak/off_peak) for this run's evaluation timestamp + model."""
    from evaluation.cost import window_for
    return window_for(result.evaluation.timestamp, result.model)
