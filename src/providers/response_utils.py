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
    """Read cached input tokens from provider usage (all known dialects).

    Chat Completions reports ``prompt_tokens_details.cached_tokens``; the
    Responses API reports the same value under
    ``input_tokens_details.cached_tokens``. Both are read, so the cached/regular
    split (a ~31x rate difference) survives whichever endpoint served the act.
    """
    if usage is None:
        return 0
    for field in ("prompt_tokens_details", "input_tokens_details"):
        details = getattr(usage, field, None) or {}
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


# ---------------------------------------------------------------------------
# Responses API (``client.responses.create``)
#
# A different dialect from Chat Completions, used by the Meta/muse-spark route
# on 9router. The differences that matter:
#
#   * tools are FLAT: {"type": "function", "name": ..., "parameters": ...} --
#     there is no nested "function" object.
#   * tool results go back as an ITEM ({"type": "function_call_output",
#     "call_id": ...}), not a role:"tool" message.
#   * the answer text is ``response.output_text``; tool calls are
#     ``item.type == "function_call"`` items in ``response.output``.
#   * the model's status is ``response.status`` ("completed"), not a
#     ``choices[0].finish_reason``.
# ---------------------------------------------------------------------------

#: Responses API statuses that mean "the model finished producing".
_RESPONSES_TERMINAL_OK = ("completed", "incomplete", "failed", "cancelled")


def flatten_tool_schemas(tools) -> list[dict]:
    """Convert Chat-Completions tool schemas to the Responses API's flat form.

    The repo's ``agents.tools.TOOL_SCHEMAS`` are written for Chat Completions
    ({"type": "function", "function": {...}}). The Responses API rejects that
    nesting, so every call site would otherwise have to remember to unwrap it.
    Schemas that are ALREADY flat are passed through, so the same helper can be
    used with either input.
    """
    flat: list[dict] = []
    for schema in tools or []:
        inner = schema.get("function") if isinstance(schema, dict) else None
        if isinstance(inner, dict):
            flat.append({"type": "function", **inner})
        else:
            flat.append(schema)
    return flat


def extract_responses_text(response) -> str:
    """Read the answer text from a Responses API object, whatever shape it has.

    Prefers ``output_text`` (the SDK's own concatenation), but falls back to
    walking ``output`` for "message" items because a gateway may omit the
    convenience field -- and a silently missing answer is exactly the failure
    mode this module exists to prevent.
    """
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text

    parts: list[str] = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", "") != "message":
            continue
        content = getattr(item, "content", None) or []
        for part in content:
            part_text = getattr(part, "text", None)
            if part_text is None and isinstance(part, dict):
                part_text = part.get("text")
            if isinstance(part_text, str):
                parts.append(part_text)
    return "".join(parts)


def extract_responses_reasoning(response) -> str:
    """Read the reasoning channel from a Responses API object.

    Reasoning arrives as ``item.type == "reasoning"`` items whose text sits in
    ``summary`` parts ({"type": "summary_text", "text": ...}), with ``content``
    normally null. Both are read so a provider change in either field does not
    silently drop the reasoning.
    """
    parts: list[str] = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", "") != "reasoning":
            continue
        for block in getattr(item, "summary", None) or []:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        content = getattr(item, "content", None)
        if isinstance(content, str) and content:
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                text = getattr(block, "text", None)
                if text is None and isinstance(block, dict):
                    text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
    return "\n".join(parts)


def extract_responses_tool_calls(response) -> list[dict]:
    """Return the function calls requested by a Responses API response.

    Each entry is ``{"call_id", "name", "arguments"}`` -- ``call_id`` is what
    the ``function_call_output`` item must carry back, so it is kept even when
    empty (the caller can then decide whether the call is usable).
    """
    calls: list[dict] = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", "") != "function_call":
            continue
        calls.append(
            {
                "call_id": getattr(item, "call_id", "") or "",
                "name": getattr(item, "name", "") or "",
                "arguments": getattr(item, "arguments", "") or "",
            }
        )
    return calls


def responses_finish_reason(response) -> str:
    """Map a Responses API status onto the ``finish_reason`` vocabulary.

    The rest of the pipeline speaks Chat Completions: ``"stop"`` means the
    model finished on its own and ``"length"`` means it was cut off by the
    output ceiling. Both matter downstream -- ``extract_diff`` and
    ``review_strategy`` branch on ``"length"`` -- so an ``incomplete`` response
    caused by ``max_output_tokens`` must NOT be flattened into a generic
    status, or a truncated act would be read as a finished one.
    """
    status = getattr(response, "status", "") or ""
    if status == "completed":
        return "stop"
    if status == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None) if details is not None else None
        if reason == "max_output_tokens":
            return "length"
        return "incomplete"
    return status


def build_responses_inference_result(
    response,
    *,
    role: str = "",
    model: str = "",
    elapsed: float = 0.0,
    response_headers: dict | None = None,
) -> InferenceResult:
    """Normalize a Responses API response into InferenceResult.

    Mirrors :func:`build_openai_inference_result` so downstream consumers see
    one shape regardless of which endpoint produced the act. Usage field names
    differ (``input_tokens`` / ``output_tokens``) and are mapped onto the
    prompt/completion names the rest of the pipeline uses.
    """
    usage = getattr(response, "usage", None)
    prompt_t = getattr(usage, "input_tokens", 0) or 0
    comp_t = getattr(usage, "output_tokens", 0) or 0
    total_t = getattr(usage, "total_tokens", 0) or (prompt_t + comp_t)
    cached_t = _extract_cached_tokens(usage)

    if response_headers is None:
        response_headers = getattr(response, "response_headers", None)
    semantic_hit, semantic_saved = extract_semantic_cache(response_headers)
    if semantic_hit and cached_t == 0:
        cached_t = prompt_t

    content = extract_responses_text(response)
    reasoning = extract_responses_reasoning(response)
    finish = responses_finish_reason(response)

    return InferenceResult(
        role=role,
        response=content,
        reasoning_content=reasoning,
        usage={
            "prompt_tokens": prompt_t,
            "completion_tokens": comp_t,
            "total_tokens": total_t,
            "cached_tokens": cached_t,
            "semantic_cache_hit": semantic_hit,
            "semantic_cache_cost_saved_usd": semantic_saved,
        },
        execution_time=elapsed,
        finish_reason=finish,
        model=model,
    )


def create_response_with_headers(client, **kwargs):
    """Call ``responses.create`` and attach HTTP response headers to the result.

    The Responses API equivalent of :func:`create_completion_with_headers`:
    the header-only response-cache signal must be readable, and the plain
    ``create()`` path does not expose headers. Falls back to a plain call for
    test doubles that only implement ``create``.
    """
    responses = getattr(client, "responses", None)
    if responses is None:
        raise AttributeError(
            "client has no responses API; cannot create a response"
        )
    raw_factory = getattr(responses, "with_raw_response", None)
    if raw_factory is None or not hasattr(raw_factory, "create"):
        return responses.create(**kwargs)

    raw = raw_factory.create(**kwargs)
    parsed = raw.parse()
    headers = getattr(raw, "headers", None)
    if headers is not None:
        try:
            parsed.response_headers = dict(headers)
        except (AttributeError, TypeError, ValueError) as exc:
            logger.warning(
                f"Could not attach response headers to the parsed response "
                f"({type(exc).__name__}: {exc}); the response-cache signal will "
                f"be reported as not-hit for this call."
            )
    return parsed


def responses_response_is_empty(response) -> bool:
    """True when a Responses API response carries NO answer and NO tool call.

    This is the silent-failure detector. Measured directly against 9router
    (2026-10-09): ``chat.completions.create`` for muse-spark returns HTTP 200
    with ``finish_reason="in_progress"``, empty content and
    ``total_tokens=0`` -- a complete-looking response that contains nothing.
    Returned as-is it would be recorded as a successful act whose answer is an
    empty string, so 150 sweep runs would fail without a single error in the
    log.

    A response with tool calls is NOT empty even when its text is empty -- that
    is normal tool calling. A response whose status is "incomplete" but which
    carries text is also not empty; the caller treats status separately.
    """
    if extract_responses_tool_calls(response):
        return False
    return not extract_responses_text(response).strip()
