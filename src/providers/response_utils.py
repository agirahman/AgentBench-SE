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


def _extract_reasoning(message) -> str:
    """Read the model's reasoning channel, whatever shape the provider returns.

    Providers disagree on the field name and the container type: DeepSeek and
    most OpenAI-compatible routes use ``reasoning_content`` (a string), some
    gateways use ``reasoning`` or ``thinking``, and a few return a LIST of parts
    (or a dict) rather than one string. Reading only ``reasoning_content`` and
    assuming a string silently discarded the reasoning of every provider that
    spelled it differently -- and since the field is optional, the loss was
    invisible: an empty string looks the same as "the model did not think".

    Used both for the act's final answer and for every turn of the tool loop, so
    a trajectory can show the reasoning that led to each tool call rather than
    only the last one.
    """
    for field in ("reasoning_content", "reasoning", "thinking"):
        value = getattr(message, field, None)
        if not value:
            continue
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(str(part) for part in value)
        if isinstance(value, dict):
            # Some gateways nest it: {"content": "..."} or {"text": "..."}.
            for key in ("content", "text", "summary"):
                inner = value.get(key)
                if isinstance(inner, str) and inner:
                    return inner
            return str(value)
        return str(value)
    return ""


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


def build_openai_inference_result(response, *, role: str = "", model: str = "", elapsed: float = 0.0, response_headers: dict | None = None) -> InferenceResult:
    """Safely normalize OpenAI-compatible provider responses into InferenceResult.

    ``response_headers`` is the raw HTTP response header dict (OpenAI SDK exposes
    it as ``response.response_headers``). When the proxy is OmniRoute, the
    ``usage`` body drops prompt-caching fields, but the cache hit is reported via
    the ``X-OmniRoute-Cache-Hit`` / ``X-OmniRoute-Cost-Saved`` headers. We recover
    the cache signal from there so the pipeline's cache metrics are not blind.
    """
    usage = getattr(response, "usage", None)
    prompt_t = getattr(usage, "prompt_tokens", 0) or 0
    comp_t = getattr(usage, "completion_tokens", 0) or 0
    total_t = getattr(usage, "total_tokens", 0) or 0
    cached_t = _extract_cached_tokens(usage)

    # Recover OmniRoute semantic-cache signal (proxy strips it from usage body).
    or_cache_hit = False
    or_cost_saved = 0.0
    if response_headers:
        hit = response_headers.get("x-omniroute-cache-hit") or response_headers.get("X-OmniRoute-Cache-Hit")
        if str(hit).strip().lower() == "true":
            or_cache_hit = True
            saved = response_headers.get("x-omniroute-cost-saved") or response_headers.get("X-OmniRoute-Cost-Saved")
            try:
                or_cost_saved = float(saved) if saved is not None else 0.0
            except (TypeError, ValueError):
                or_cost_saved = 0.0
        # On a semantic-cache HIT the entire prompt was served from cache: treat
        # the full prompt token count as cached so downstream cost/cache metrics
        # reflect the hit (only when the body itself reported no cached tokens).
        if or_cache_hit and cached_t == 0:
            cached_t = prompt_t

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
            reasoning = _extract_reasoning(message)
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
            "omniroute_cache_hit": or_cache_hit,
            "omniroute_cost_saved_usd": or_cost_saved,
        },
        execution_time=elapsed,
        finish_reason=finish,
        model=model,
    )
