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


# Wire literals for the response-cache signal. These are the names the proxy
# actually sends on the wire; they are technical constants, not feature naming.
# The feature itself is called "semantic cache" / "response cache" throughout
# the codebase so it stays provider-neutral.
SEMANTIC_CACHE_HIT_HEADER = "x-omniroute-cache-hit"
SEMANTIC_CACHE_COST_SAVED_HEADER = "x-omniroute-cost-saved"


def _header_value(headers, name: str):
    """Read a header case-insensitively from either a dict or an ``httpx.Headers``.

    ``httpx.Headers`` is case-insensitive already; a plain dict is not, and the
    proxy does not promise a casing. Normalising both means a signal that only
    arrives in one spelling cannot go missing.
    """
    if not headers:
        return None
    getter = getattr(headers, "get", None)
    if getter is not None:
        try:
            value = getter(name)
        except (TypeError, AttributeError):
            value = None
        if value is not None:
            return value
    target = name.lower()
    try:
        items = headers.items()
    except AttributeError:
        return None
    for key, value in items:
        if str(key).lower() == target:
            return value
    return None


def extract_semantic_cache(headers) -> tuple[bool, float]:
    """Read the semantic (response) cache signal from HTTP response headers.

    Returns ``(hit, cost_saved_usd)``.

    This is a DIFFERENT signal from prefix/prompt caching. Prefix caching
    (``cached_tokens``, normally 76-81% of the prompt) is the provider reusing
    the KV state of a shared prefix; it is expected and harmless. A semantic
    cache hit means the provider replayed an ENTIRE previous response instead of
    running the model, so a run that hits it did not measure the strategy at all
    and must not be compared against the others.

    The proxy reports it only in headers -- the ``usage`` body does not carry it
    -- so this is the only place the signal exists.
    """
    raw = _header_value(headers, SEMANTIC_CACHE_HIT_HEADER)
    if str(raw).strip().lower() != "true":
        return False, 0.0
    saved = _header_value(headers, SEMANTIC_CACHE_COST_SAVED_HEADER)
    try:
        return True, float(saved) if saved is not None else 0.0
    except (TypeError, ValueError):
        return True, 0.0


def create_completion_with_headers(client, **kwargs):
    """Call the API and attach the HTTP response headers to the parsed result.

    A plain ``client.chat.completions.create()`` returns a parsed ``ChatCompletion``
    that does NOT carry the response headers -- verified against the installed SDK
    (``openai`` 2.45.0): the model has no such field and the attribute does not
    exist on the class or an instance. Every header-only signal is therefore
    unreadable through that path, which is exactly how the response-cache flag
    stayed invisible. Going through ``with_raw_response`` keeps the same kwargs
    and the same exception behaviour (a 429 still raises ``RateLimitError``,
    verified with a mock transport) while exposing ``.headers``.

    Falls back to a plain call when the client does not offer the raw path --
    notably the fake clients in the test suite, which only implement ``create``.
    """
    completions = getattr(getattr(client, "chat", None), "completions", None)
    if completions is None:
        raise AttributeError(
            "client has no chat.completions; cannot create a completion"
        )
    raw_factory = getattr(completions, "with_raw_response", None)
    if raw_factory is None or not hasattr(raw_factory, "create"):
        # Fake/limited clients (including the test doubles) only implement
        # ``create``; they simply carry no header signal.
        return completions.create(**kwargs)

    raw = raw_factory.create(**kwargs)
    parsed = raw.parse()
    headers = getattr(raw, "headers", None)
    if headers is not None:
        try:
            parsed.response_headers = dict(headers)
        except (AttributeError, TypeError, ValueError) as exc:
            # Never fail a paid run over a diagnostic attachment. The signal is
            # lost for this response and reported as "no hit", which is the same
            # state the pipeline was in before -- not a new failure mode.
            logger.warning(
                f"Could not attach response headers to the parsed response "
                f"({type(exc).__name__}: {exc}); the response-cache signal will "
                f"be reported as not-hit for this call."
            )
    return parsed


def build_openai_inference_result(response, *, role: str = "", model: str = "", elapsed: float = 0.0, response_headers: dict | None = None) -> InferenceResult:
    """Safely normalize OpenAI-compatible provider responses into InferenceResult.

    ``response_headers`` is the raw HTTP response header mapping, attached by
    :func:`create_completion_with_headers` (a plain ``create()`` does not expose
    it). The proxy drops prompt-caching fields from the ``usage`` body but
    reports the response-cache hit in headers, so the signal is recovered here --
    otherwise the pipeline's cache metrics are blind to it.
    """
    usage = getattr(response, "usage", None)
    prompt_t = getattr(usage, "prompt_tokens", 0) or 0
    comp_t = getattr(usage, "completion_tokens", 0) or 0
    total_t = getattr(usage, "total_tokens", 0) or 0
    cached_t = _extract_cached_tokens(usage)

    # Recover the response-cache signal (the proxy strips it from the usage body).
    # When the caller did not pass headers explicitly, fall back to the ones
    # attached to the response by create_completion_with_headers, so a provider
    # that only forwards the response object still carries the signal.
    if response_headers is None:
        response_headers = getattr(response, "response_headers", None)
    semantic_hit, semantic_saved = extract_semantic_cache(response_headers)
    if semantic_hit:
        # On a HIT the entire prompt was served from cache: treat the full prompt
        # token count as cached so downstream cost/cache metrics reflect the hit
        # (only when the body itself reported no cached tokens).
        if cached_t == 0:
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
            # Neutral names: the signal is a provider feature (semantic /
            # response cache), not a brand. The wire header literal stays in
            # SEMANTIC_CACHE_HIT_HEADER above.
            "semantic_cache_hit": semantic_hit,
            "semantic_cache_cost_saved_usd": semantic_saved,
        },
        execution_time=elapsed,
        finish_reason=finish,
        model=model,
    )
