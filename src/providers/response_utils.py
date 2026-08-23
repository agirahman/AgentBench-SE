from models.inference import InferenceResult
from utils.logger import logger


def _extract_content(message) -> str:
    content = getattr(message, "content", "") or ""
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or ""
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    if not isinstance(content, str):
        return str(content)
    return content


def _extract_cached_tokens(usage) -> int:
    """Read cached input tokens from provider usage (OpenAI / DeepSeek formats)."""
    if usage is None:
        return 0
    details = getattr(usage, "prompt_tokens_details", None) or {}
    if isinstance(details, dict):
        cached = details.get("cached_tokens", 0) or 0
    else:
        cached = getattr(details, "cached_tokens", 0) or 0
    if cached:
        return cached
    return getattr(usage, "prompt_cache_hit_tokens", 0) or 0


def build_openai_inference_result(response, *, role: str = "", model: str = "", elapsed: float = 0.0) -> InferenceResult:
    """Safely normalize OpenAI-compatible provider responses into InferenceResult."""
    usage = getattr(response, "usage", None)
    prompt_t = getattr(usage, "prompt_tokens", 0) or 0
    comp_t = getattr(usage, "completion_tokens", 0) or 0
    total_t = getattr(usage, "total_tokens", 0) or 0
    cached_t = _extract_cached_tokens(usage)

    choices = getattr(response, "choices", None) or []
    content = ""
    finish = ""
    reasoning = ""

    if choices:
        choice = choices[0]
        finish = getattr(choice, "finish_reason", "") or ""
        message = getattr(choice, "message", None)
        if message is not None:
            content = _extract_content(message)
            reasoning = getattr(message, "reasoning_content", "") or ""
            if isinstance(reasoning, list):
                reasoning = "".join(str(p) for p in reasoning)
            elif not isinstance(reasoning, str):
                reasoning = str(reasoning)
    else:
        finish = "EMPTY_RESPONSE"
        logger.warning(f"Provider returned no choices for role={role!r} model={model!r}")

    if not content and reasoning:
        logger.warning(
            f"Response has empty content but non-empty reasoning for role={role!r} model={model!r} "
            f"finish_reason={finish!r} reasoning_len={len(reasoning)} — "
            f"budget likely consumed by thinking mode"
        )

    return InferenceResult(
        role=role,
        response=content,
        reasoning_content=reasoning,
        usage={
            "prompt_tokens": prompt_t,
            "completion_tokens": comp_t,
            "total_tokens": total_t,
            "cached_tokens": cached_t,
        },
        execution_time=elapsed,
        finish_reason=finish,
        model=model,
    )
