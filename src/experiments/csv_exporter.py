from models.result import ExperimentResult


def flatten_for_csv(result: ExperimentResult) -> dict:
    """Flatten nested ExperimentResult → flat dict for CSV export."""
    return {
        "instance_id": result.instance_id,
        "strategy": result.strategy,
        "model": result.model,
        "difficulty": result.difficulty,
        "inference_count": result.execution.inference_count,
        "execution_time": result.execution.execution_time,
        "prompt_tokens": result.execution.prompt_tokens,
        "completion_tokens": result.execution.completion_tokens,
        "total_tokens": result.execution.total_tokens,
        "patch_preview": result.execution.patch_preview,
        "input_cost_usd": result.cost.input_cost_usd,
        "output_cost_usd": result.cost.output_cost_usd,
        "cost_usd": result.cost.total_cost_usd,
        "cost_idr": result.cost.total_cost_idr,
        "pricing_version": result.cost.pricing_version,
        "success": result.evaluation.success,
        "error": result.evaluation.error,
        "patch_status": result.patch_status,
        "timestamp": result.evaluation.timestamp,
    }
