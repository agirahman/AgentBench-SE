from config import Config
from models.result import ExperimentResult


def flatten_for_csv(result: ExperimentResult) -> dict:
    """Flatten nested ExperimentResult → flat dict for CSV export.

    Column layout (sectioned, paired token→cost):
      1. Meta  : id, strategy, model, difficulty, inference_count,
                 total_tool_calls, api_turns, total_turns, truncated_acts,
                 truncated, execution_time,
                 timestamp, timestamp_wib, window, pricing_version, generated, error,
                 semantic_cache_hit, semantic_cache_cost_saved_usd,
                 semantic_cache_hit_turns
      2. INPUT (token → cost paired, USD + IDR):
                 input_tokens_cached, input_cost_usd_cached, input_cost_idr_cached,
                 input_tokens_regular, input_cost_usd_regular, input_cost_idr_regular,
                 input_tokens_total, input_cost_usd_total, input_cost_idr_total
      3. OUTPUT:
                 output_tokens, output_cost_usd, output_cost_idr
      4. TOTAL:
                 total_tokens, cost_usd_offpeak, cost_idr_offpeak
      5. PRICING VARIANTS (total only):
                 cost_usd_peak, cost_idr_peak, cost_usd_actual, cost_idr_actual,
                 cost_usd_peak_total, cost_idr_peak_total
      6. PATCH (right-aligned):
                 patch_status, raw_regex_fixed, hunk_mismatch_resolved, patch_preview
    """
    inferences = result.execution.inferences

    # API-call accounting
    total_turns = sum(getattr(inf, "api_turns", 1) for inf in inferences) if inferences else 0
    first_api_turns = getattr(inferences[0], "api_turns", 1) if inferences else 1
    total_tool_calls = sum(len(getattr(inf, "tool_calls", []) or []) for inf in inferences)
    # How many acts were cut off by their turn cap. A truncated run's outcome
    # measures the granted budget, not the strategy, so it must be reportable
    # rather than inferred from logs afterwards (SWE-bench reports this class
    # separately and keeps it in the denominator).
    truncated_acts = sum(1 for inf in inferences if getattr(inf, "truncated", False))

    # Tokens
    cached_tok = result.cost.cached_input_tokens
    regular_tok = result.cost.regular_input_tokens
    input_total_tok = cached_tok + regular_tok
    output_tok = result.execution.completion_tokens
    total_tok = result.execution.total_tokens

    # Cost components (off-peak)
    input_cost_cached_usd = result.cost.cached_input_cost_usd
    input_cost_regular_usd = result.cost.regular_input_cost_usd
    input_cost_total_usd = input_cost_cached_usd + input_cost_regular_usd
    output_cost_usd = result.cost.output_cost_usd
    cost_usd_offpeak = result.cost.total_cost_usd

    # IDR conversion (using Config rate, consistent with downstream)
    rate = Config.USD_IDR_RATE
    input_cost_cached_idr = input_cost_cached_usd * rate
    input_cost_regular_idr = input_cost_regular_usd * rate
    input_cost_total_idr = input_cost_total_usd * rate
    output_cost_idr = output_cost_usd * rate
    cost_idr_offpeak = cost_usd_offpeak * rate

    return {
        # ── Meta ──
        "instance_id": result.instance_id,
        "strategy": result.strategy,
        "model": result.model,
        "difficulty": result.difficulty,
        "inference_count": result.execution.inference_count,
        "total_tool_calls": total_tool_calls,
        "api_turns": first_api_turns,
        "total_turns": total_turns,
        "truncated_acts": truncated_acts,
        "truncated": truncated_acts > 0,
        "execution_time": result.execution.execution_time,
        "timestamp": result.evaluation.timestamp,
        "timestamp_wib": _to_wib(result.evaluation.timestamp),
        "window": _window_of(result),
        "pricing_version": result.cost.pricing_version,
        "generated": result.evaluation.success,
        "error": result.evaluation.error,
        # Explicit per-run integrity flag: True means the provider replayed a
        # cached response, so the run did not measure the strategy. Kept as its
        # own column (NOT inferred from the cached-token ratio) so a run that hit
        # it is visible in the exported results rather than silently averaged in.
        "semantic_cache_hit": bool(result.cost.semantic_cache_hit),
        "semantic_cache_cost_saved_usd": result.cost.semantic_cache_cost_saved_usd,
        "semantic_cache_hit_turns": result.cost.semantic_cache_hit_turns,

        # ── INPUT (token → cost paired) ──
        "input_tokens_cached": cached_tok,
        "input_cost_usd_cached": input_cost_cached_usd,
        "input_cost_idr_cached": input_cost_cached_idr,
        "input_tokens_regular": regular_tok,
        "input_cost_usd_regular": input_cost_regular_usd,
        "input_cost_idr_regular": input_cost_regular_idr,
        "input_tokens_total": input_total_tok,
        "input_cost_usd_total": input_cost_total_usd,
        "input_cost_idr_total": input_cost_total_idr,

        # ── OUTPUT (token → cost paired) ──
        "output_tokens": output_tok,
        "output_cost_usd": output_cost_usd,
        "output_cost_idr": output_cost_idr,

        # ── TOTAL ──
        "total_tokens": total_tok,
        "cost_usd_offpeak": cost_usd_offpeak,
        "cost_idr_offpeak": cost_idr_offpeak,

        # ── PRICING VARIANTS (total only) ──
        "cost_usd_peak": result.cost.peak_total_cost_usd,
        "cost_idr_peak": result.cost.peak_total_cost_idr,
        "cost_usd_actual": result.cost.actual_cost_usd,
        "cost_idr_actual": result.cost.actual_cost_idr,
        "cost_usd_peak_total": result.cost.peak_total_cost_usd,
        "cost_idr_peak_total": result.cost.peak_total_cost_idr,

        # ── PATCH (right) ──
        "patch_status": result.patch_status,
        "apply_status": result.apply_status,
        "raw_regex_fixed": result.patch_status == "NORMALIZE",
        "hunk_mismatch_resolved": result.patch_status == "NORMALIZE",
        "patch_preview": result.execution.patch_preview,
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
