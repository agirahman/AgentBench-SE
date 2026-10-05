"""Shared tool-calling loop for OpenAI-compatible providers.

The loop lives in the pipeline, not on the server: an OpenAI-compatible API is
stateless and has no execution capability. The server only *decides* which tool
to call; this module executes it locally and sends the result back as a
``role: "tool"`` message. Skipping that round-trip would make the model repeat
the same call forever.

Kept provider-agnostic so ``commandcode`` (9router) and ``openrouter`` share one
implementation instead of drifting apart.

Retry happens HERE, per request, not around the whole loop. Wrapping the loop in
``@with_retry`` meant one HTTP timeout restarted the conversation from scratch:
the exploration gathered so far was discarded and the tool-turn budget was
handed out a second time. Measured on EXP-20260928-001 (django-11019, direct):
45 + 16 + 38 = 99 tool calls against a budget of 60, 152 minutes for one
strategy. A retry inside the loop resends the failed turn only, so the budget
stays a real bound.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Optional

from config import Config
from utils.logger import logger
from models.inference import InferenceResult
from evaluation.cost import PricingTable
from evaluation.retry import call_with_retry
from providers.response_utils import (
    build_openai_inference_result,
    create_completion_with_headers,
    extract_semantic_cache,
    _extract_cached_tokens,
    _extract_content,
    _extract_reasoning,
)
from providers.system_prompts import TOOL_SYSTEM_PROMPT, EDITING_ROLES as _EDITING_ROLES
from agents.tools import (
    TOOL_SCHEMAS,
    execute_tool,
    reset_test_guard,
    set_repo_root,
)


def _truncate_tool_output(text: str, limit: int) -> str:
    """Cap a tool result, keeping the head AND the tail.

    The tail is what matters for diagnostics: test output, tracebacks and
    ``git diff`` summaries put the actual error or the changed-file list last,
    so a plain head-only slice hid exactly the line the model needed. The
    dropped part is the middle.

    Each turn re-sends the whole conversation, so this value multiplies by the
    number of turns: at 8000 chars/turn a 60-turn run accumulated ~480 KB of
    tool output alone, which is what pushed late requests past the API timeout.
    """
    if limit <= 0 or len(text) <= limit:
        return text
    half = max(1, limit // 2)
    head, tail = text[:half], text[-half:]
    dropped = len(text) - len(head) - len(tail)
    return f"{head}\n… [{dropped} chars omitted] …\n{tail}"


def _cost_so_far(usage_totals: dict, rates: dict) -> float:
    """Cost of the tokens accumulated so far, using a per-million rate card.

    Split into cached and regular input because the two rates differ by ~31x
    ($0.007/M vs $0.22/M on the DeepSeek card). Charging the whole prompt at the
    regular rate would overstate cost by an order of magnitude -- measured on
    EXP-20260928-003, 90.7% of input was cached.

    This function is the RUNAWAY GUARD's estimate, and it deliberately uses a
    different card from the one that produces the reported RQ3 cost. The two must
    not be conflated:

    * The guard answers "is this act about to spend too much REAL money?", so it
      is priced with the rate the upstream actually bills. On the cbai route that
      is $0.15/$0.003/$0.28-ish, roughly 4.9x cheaper than DeepSeek's official
      reference price. Armed with the official card instead, a $3.00 cap would
      stop the run after ~$0.62 of real spend -- the bound, not the agent, would
      decide where the act ended, and the turn-budget fairness invariant would
      silently become a dollar-budget one.

    * The reported cost answers "what does this cost at published reference
      prices?", which is what a thesis must be able to defend. That lives in
      evaluation/cost.py and is not this function.

    Keeping them separate is the point: neither number is allowed to drift into
    the other's job.
    """
    cached = usage_totals.get("cached_tokens", 0) or 0
    prompt = usage_totals.get("prompt_tokens", 0) or 0
    regular = max(0, prompt - cached)
    completion = usage_totals.get("completion_tokens", 0) or 0
    return (
        regular / 1_000_000 * rates.get("input_per_million", 0.0)
        + cached / 1_000_000 * rates.get("cached_input_per_million", 0.0)
        + completion / 1_000_000 * rates.get("output_per_million", 0.0)
    )


def _trim_messages_for_context(messages: list[dict], keep_chars: int) -> None:
    """Shrink the oldest tool outputs in place so one more request can fit.

    Only called when a request has ALREADY been rejected for context length, so
    the goal is narrow: make the next request fit without discarding the recent
    evidence the agent needs to answer. The most recent tool results are kept
    intact and the oldest are cut to a short head, because a decision about what
    to do next depends on what was just read, not on the first file opened.

    Rebuilding the conversation from scratch (the alternative) would throw away
    the whole exploration, which is exactly the failure the per-request retry in
    this module exists to avoid.
    """
    tool_indexes = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if not tool_indexes:
        return

    # Keep the last few results whole; shrink everything before them.
    protected = set(tool_indexes[-3:])
    for idx in tool_indexes:
        if idx in protected:
            continue
        content = messages[idx].get("content") or ""
        if len(content) <= keep_chars:
            continue
        messages[idx]["content"] = (
            content[: keep_chars // 2]
            + "\n… [trimmed to fit the model's context limit] …\n"
        )


def _retryable_response(resp) -> bool:
    """True when a single response would end the loop with nothing.

    A response WITH tool calls is normal even when its text is empty — that is
    how tool calling looks. Only "no tool call and no text" is a dead end worth
    resending, and resending it costs one request rather than the whole
    conversation.
    """
    try:
        msg = resp.choices[0].message
    except (AttributeError, IndexError, TypeError):
        return False
    if getattr(msg, "tool_calls", None):
        return False
    return not (getattr(msg, "content", "") or "").strip()


def _is_context_overflow(exc: Exception) -> bool:
    """True when the provider rejected a request because the prompt is too long.

    A context overflow is NOT a transient failure, so it must not be retried: the
    conversation only grows, so the same request fails again, and each retry pays
    for the tokens again. Worse, ``call_with_retry`` would classify it as a
    provider error and the act would be marked failed -- reporting a context limit
    as a strategy failure.

    Matched by message text because providers disagree on the status code (some
    use 400, some 413) and the OpenAI SDK surfaces the body text.
    """
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "context length",
            "context_length",
            "maximum context",
            "too many tokens",
            "reduce the length",
            "prompt is too long",
            "input is too long",
        )
    )


def run_tool_loop(
    client,
    *,
    model: str,
    prompt: str,
    role: str = "",
    tools: Optional[list] = None,
    max_tool_turns: Optional[int] = None,
    max_cost_usd: Optional[float] = None,
    max_wall_seconds: Optional[float] = None,
    repo_root: Optional[str] = None,
    temperature: Optional[float] = None,
    timeout: Optional[int] = None,
    max_tokens: Optional[int] = None,
    extra_body: Optional[dict] = None,
    system_prompt: str = TOOL_SYSTEM_PROMPT,
) -> InferenceResult:
    """Run a tool-calling conversation and return the final answer.

    ``result.response`` is the LAST assistant text (tool messages excluded).
    ``result.tool_calls`` records ``{"name", "arguments", "result"}`` for every
    executed call, and ``result.api_turns`` reports how many HTTP turns the loop
    actually used — token/turn metrics would otherwise undercount tool-heavy
    runs, where each turn re-sends the whole growing conversation.

    ``api_turns`` counts LOGICAL turns, not HTTP attempts: a request that times
    out and is retried still consumed one turn, so a retry can never enlarge the
    budget the strategy granted. Token usage, by contrast, counts every request
    actually paid for, retries included.

    ``max_cost_usd`` is a runaway guard, not a tuning knob. SWE-agent and
    mini-SWE-agent both bound a task by dollars ($3) and we bounded turns only,
    so a strategy that spends 2x tokens for the same turns was unconstrained.
    The caller passes what remains of the TASK budget; the loop enforces it and
    marks the result truncated, because an act stopped for cost was cut off for
    exactly the same reason as one stopped for turns: the bound, not the model's
    own judgement, decided where it ended.

    ``max_wall_seconds`` bounds the act in WALL-CLOCK time, which the turn and
    cost guards do not. A turn budget does not bound duration when every request
    retries: with RATE_LIMIT_BACKOFF_BASE=60 and MAX_RETRIES=3, one request can
    wait 60+120 = 180 s before failing, so a 40-turn act could spend over an hour
    in backoff alone and a three-act strategy over three. Measured: a single
    review run took 5,992 s (100 min) at pool 40. Across 150 runs that is hours
    of pure sleeping, and the rate-limit circuit breaker never trips because the
    backoff happens INSIDE this loop and never surfaces as a failure to the
    runner. ``None`` keeps the previous unbounded behaviour.
    """
    tools = tools or TOOL_SCHEMAS
    max_tool_turns = max_tool_turns or Config.MAX_TOOL_TURNS
    # Default the wall-clock bound from config so all three providers inherit it
    # without each having to thread a new parameter through. Explicit argument
    # wins; 0 disables.
    if max_wall_seconds is None:
        configured = getattr(Config, "ACT_TIMEOUT_SECONDS", 0) or 0
        max_wall_seconds = configured if configured > 0 else None
    temperature = Config.TEMPERATURE if temperature is None else temperature
    timeout = Config.API_TIMEOUT if timeout is None else timeout
    max_tokens = Config.MAX_TOKENS if max_tokens is None else max_tokens
    output_limit = Config.TOOL_OUTPUT_MAX_CHARS
    t0 = time.perf_counter()

    # Rate card for the cost guard. None when no cap was requested, so the loop
    # does no pricing work at all in the default (uncapped) path.
    #
    # `is not None`, not truthiness: an EXHAUSTED cap arrives as 0.0 and must
    # still arm the guard. Testing truthiness would read "budget spent" as "no
    # budget", so the run would keep spending precisely when it should stop.
    #
    # PricingTable.get() honours PRICING_MODEL_OVERRIDE, so a free testing model
    # is priced with the paid card when the curve runs ask for it. Without that
    # the rates are all 0.0, the guard can never bind, and the cost columns stay
    # empty -- the exact hole EXP-20260928-003 fell into.
    #
    # The guard uses the BILLED card when one is configured, not the reporting
    # card: it is bounding real money. Config.COST_GUARD_MODEL names the model
    # whose rate card should price the cap; when it is unset we fall back to the
    # reporting card, which is correct for routes where the two coincide.
    capped = max_cost_usd is not None
    guard_model = getattr(Config, "COST_GUARD_MODEL", "") or model
    cost_rates = PricingTable.rates_for(guard_model, "off_peak") if capped else None
    cost_exceeded = False
    wall_exceeded = False
    context_exceeded = False

    # Always sync the sandbox to THIS instance's repo root. Passing None resets
    # it to the shared base — never silently reuse a previous instance's root.
    set_repo_root(repo_root)
    # ...and start this act with a clean repeat-call history. The guard exists to
    # stop ONE agent from spinning on one command; a strategy has several agents
    # (review: planner, executor, reviewer, revision) that share neither a turn
    # budget nor a conversation, so carrying one act's history into the next would
    # refuse a later agent a command it never ran. Measured: on
    # django__django-11001 the planning executor and the review reviewer issued the
    # IDENTICAL pytest command, and without this reset the second one would be
    # refused -- silently, and through no fault of its own.
    reset_test_guard()

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    recorded_calls: list[dict] = []
    # Full turn-by-turn record. The provider's own ``messages`` list is discarded
    # when the act ends, so without this the only surviving trace of an act was
    # its final answer plus a flat call list: the reasoning behind each step, and
    # what the agent saw when it decided, were both gone. Kept separate from
    # ``recorded_calls`` (which stays for the existing per-call summary) so no
    # consumer has to change at once.
    trajectory: list[dict] = []
    usage_totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        # Accumulated across every request of this act. A single hit is enough to
        # disqualify the act from strategy comparison, so these are OR/SUM rather
        # than an average: the flag must survive even when 59 of 60 turns missed.
        "semantic_cache_hit": False,
        "semantic_cache_cost_saved_usd": 0.0,
        # How many REQUESTS hit (not a boolean): "one request happened to repeat"
        # and "the whole act was replayed" are very different findings, and the
        # boolean alone cannot tell them apart.
        "semantic_cache_hit_turns": 0,
    }
    api_turns = 0

    def _base_kwargs() -> dict:
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "timeout": timeout,
            "max_tokens": max_tokens,
        }
        if extra_body:
            kwargs["extra_body"] = extra_body
        return kwargs

    def _record_usage(resp) -> None:
        """Accumulate tokens for a request we actually paid for.

        Called on every successful HTTP response, including one that is about to
        be retried for empty content — that request was still billed.

        The response-cache signal is accumulated HERE, not read once at the end.
        ``_finalize`` replaces ``result.usage`` with ``usage_totals``, so any
        signal that only lived on the final response object was discarded — and
        because every sweep run goes through this loop, the flag was lost for
        every run, making a cache hit impossible to detect after the fact. The
        header is on EVERY response, so it must be collected on every response.
        """
        # Read the cache signal BEFORE the usage guard: it travels in the HTTP
        # headers, not the body, so it can be present on a response whose usage
        # block is missing. Returning early on `u is None` would drop exactly the
        # signal this function exists to preserve.
        hit, saved = extract_semantic_cache(getattr(resp, "response_headers", None))
        if hit:
            # Sticky: one hit anywhere in the act is enough to taint it, so this
            # is never reset back to False by a later miss.
            usage_totals["semantic_cache_hit"] = True
            usage_totals["semantic_cache_cost_saved_usd"] += saved
            usage_totals["semantic_cache_hit_turns"] += 1

        u = getattr(resp, "usage", None)
        if u is None:
            return
        pt = getattr(u, "prompt_tokens", 0) or 0
        ct = getattr(u, "completion_tokens", 0) or 0
        tt = getattr(u, "total_tokens", 0) or (pt + ct)
        usage_totals["prompt_tokens"] += pt
        usage_totals["completion_tokens"] += ct
        usage_totals["total_tokens"] += tt
        usage_totals["cached_tokens"] += _extract_cached_tokens(u)

    def _create(kwargs: dict, label: str):
        """One request, retried in place on failure or empty content.

        The retry wraps this single call rather than the loop, so a timeout on
        turn N resends turn N: the conversation so far and the turn budget are
        both untouched.
        """

        def _attempt():
            # Goes through the raw-response path so the HTTP headers (which carry
            # the response-cache signal) are attached to the parsed object. A
            # plain create() returns a model with no header field at all, so the
            # signal would be unreadable no matter what this loop did with it.
            resp = create_completion_with_headers(client, **kwargs)
            _record_usage(resp)
            return resp

        return call_with_retry(
            _attempt,
            retry_on=_retryable_response,
            # A context overflow cannot be cured by resending: the conversation
            # only grows. Retrying it burns the backoff schedule AND pays for the
            # same oversized prompt again, and it hides the error from the caller's
            # own handler until the final attempt.
            fatal_on=_is_context_overflow,
            label=label,
        )

    def _finalize(resp) -> InferenceResult:
        elapsed = time.perf_counter() - t0
        result = build_openai_inference_result(
            resp,
            role=role,
            model=model,
            elapsed=elapsed,
            response_headers=getattr(resp, "response_headers", None),
        )
        result.usage = dict(usage_totals)
        result.tool_calls = recorded_calls
        result.api_turns = max(1, api_turns)
        result.trajectory = list(trajectory)
        return result

    for turn in range(1, max_tool_turns + 1):
        # Wall-clock guard: stop BEFORE starting a turn that would run past the
        # act's time bound. The turn and cost guards cannot bound duration -- with
        # retries, one request may sit in backoff for minutes -- so without this a
        # run can hang for hours while making no progress and never tripping the
        # runner's rate-limit breaker, which only sees failures that surface.
        if max_wall_seconds is not None:
            elapsed_wall = time.perf_counter() - t0
            if elapsed_wall >= max_wall_seconds:
                logger.warning(
                    f"Tool loop hit max_wall_seconds={max_wall_seconds:.0f} for "
                    f"role={role} after {turn - 1} turn(s) "
                    f"({elapsed_wall:.0f}s elapsed) -- stopping"
                )
                wall_exceeded = True
                break

        # Cost guard: stop BEFORE issuing a request that would exceed the cap.
        # Checked at the top of the turn so the guard bounds what we spend, not
        # what we already spent. Prefix caching makes the check cheap to satisfy
        # in practice -- measured on EXP-20260928-003, 90.7% of input tokens were
        # cached at $0.007/M against $0.22/M regular.
        if cost_rates is not None and _cost_so_far(usage_totals, cost_rates) >= max_cost_usd:
            logger.warning(
                f"Tool loop hit max_cost_usd={max_cost_usd} for role={role} "
                f"after {turn - 1} turn(s) (spent "
                f"${_cost_so_far(usage_totals, cost_rates):.4f})"
            )
            cost_exceeded = True
            break

        kwargs = _base_kwargs()
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

        # Budget pressure: the loop previously ran out of turns with no final
        # answer (13 occurrences in the logs) because nothing told the model to
        # stop exploring. Nudge it to wrap up as the budget runs low.
        #
        # The nudge must match the role's mandate: telling a read-only role
        # (planner, reviewer) to "apply your fix with edit_file" is an instruction
        # it cannot obey — it has no edit_file tool — and it invites the model to
        # narrate an edit that never happens instead of returning its verdict.
        remaining = max_tool_turns - turn
        if remaining <= 1:
            if role in _EDITING_ROLES:
                wrap_up = (
                    "You are almost out of tool budget. Stop exploring and apply "
                    "your fix NOW with edit_file, then reply with a one-line "
                    "summary and no tool call."
                )
            else:
                wrap_up = (
                    "You are almost out of tool budget. Stop exploring and reply "
                    "NOW with your final answer and no tool call. Do not call any "
                    "more tools."
                )
            messages.append({"role": "user", "content": wrap_up})

        try:
            response = _create(kwargs, f"tool_loop[{role}] turn {turn}")
        except Exception as exc:  # noqa: BLE001
            # A context overflow is terminal, not transient: the conversation only
            # grows, so retrying the same request fails again and is billed again.
            # Stop the act and ask for a final answer from what is already known,
            # the same recovery the turn/cost/clock bounds use. Without this the
            # act is marked FAILED by the provider-error path, which reports a
            # context limit as a strategy failure.
            if not _is_context_overflow(exc):
                raise
            context_exceeded = True
            logger.warning(
                f"Tool loop hit the model's context limit for role={role} after "
                f"{turn - 1} turn(s) -- stopping and requesting a final answer "
                f"({str(exc)[:160]})"
            )
            break
        api_turns += 1
        choice = response.choices[0]
        msg = choice.message

        # Record the assistant turn BEFORE branching on whether it called a tool,
        # so the turn that ENDS the act is in the trajectory too. Recording only
        # turns with tool calls would drop the final answer from the record and
        # make it look like the act stopped mid-flight.
        assistant_entry = {
            "type": "assistant",
            "turn": turn,
            "role": role,
            "content": getattr(msg, "content", "") or "",
            "reasoning": _extract_reasoning(msg),
            "finish_reason": getattr(choice, "finish_reason", "") or "",
            "tool_calls": [
                {
                    "id": getattr(tc, "id", ""),
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                }
                for tc in (getattr(msg, "tool_calls", None) or [])
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        trajectory.append(assistant_entry)

        if not getattr(msg, "tool_calls", None):
            return _finalize(response)

        messages.append(
            {
                "role": "assistant",
                "content": getattr(msg, "content", "") or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
        )

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            tool_out = execute_tool(name, args, role=role)
            recorded_calls.append(
                {"name": name, "arguments": args, "result": tool_out[:2000]}
            )
            logger.info(f"[toolcall] role={role} tool={name} args={args}")
            # The trajectory keeps the FULL result; the summary above keeps the
            # 2000-char preview. Truncating here would defeat the point of a
            # trajectory: the line that explains a decision is often the one a
            # preview cuts. Size is bounded by the run itself, not by this field.
            trajectory.append(
                {
                    "type": "tool",
                    "turn": turn,
                    "role": role,
                    "tool_call_id": getattr(tc, "id", ""),
                    "name": name,
                    "arguments": args,
                    "result": tool_out,
                    "result_chars": len(tool_out),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": _truncate_tool_output(tool_out, output_limit),
                }
            )

    # Out of turns, out of money, or out of time: ask for a final answer WITHOUT
    # tools so the model cannot call another one and loop again. Either way the act
    # was CUT OFF by a bound rather than finishing on its own, so all paths mark the
    # result truncated -- an act stopped by cost or by the clock is exactly as
    # unattributable to the strategy as one stopped by turns.
    if wall_exceeded:
        stop_reason = "wall_clock"
        logger.warning(
            f"Tool loop stopped on wall clock for role={role} "
            f"(bound {max_wall_seconds:.0f}s, elapsed {time.perf_counter() - t0:.0f}s)"
        )
    elif context_exceeded:
        stop_reason = "context_limit"
        # No extra log line: the warning was already emitted at the point of
        # failure, where the turn number is known.
    elif cost_exceeded:
        stop_reason = "cost"
        logger.warning(
            f"Tool loop stopped on cost for role={role} "
            f"(cap ${max_cost_usd}, spent ${_cost_so_far(usage_totals, cost_rates or {}):.4f})"
        )
    else:
        stop_reason = "max_tool_turns"
        logger.warning(f"Tool loop hit max_tool_turns={max_tool_turns} for role={role}")

    # Mark the cutoff in the trajectory itself. Reading it only from the log meant
    # the record showed a run that "ended" with a summary, indistinguishable from
    # one that finished on its own -- the exact confusion that made EXP-003's
    # 8/8/6 headline number unreadable.
    trajectory.append(
        {
            "type": "bound_reached",
            "turn": api_turns,
            "role": role,
            "stop_reason": stop_reason,
            "granted_turns": max_tool_turns,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )

    # Ask for a final answer without tools. The one case where this cannot work is
    # a CONTEXT overflow: the conversation is still too long, so the same request
    # fails again -- and would raise out of the loop, losing the whole act. Trim the
    # oldest tool outputs first so the request fits, because an act that returns a
    # partial answer is worth far more than one that returns nothing.
    if context_exceeded:
        _trim_messages_for_context(messages, output_limit)

    try:
        response = _create(_base_kwargs(), f"tool_loop[{role}] final-answer")
    except Exception as exc:  # noqa: BLE001
        if not _is_context_overflow(exc):
            raise
        logger.warning(
            f"Tool loop could not produce a final answer within the context limit "
            f"for role={role}; returning what was established "
            f"({str(exc)[:120]})"
        )
        result = InferenceResult(
            role=role,
            response="",
            usage=dict(usage_totals),
            execution_time=time.perf_counter() - t0,
            finish_reason="context_limit",
            model=model,
            tool_calls=recorded_calls,
            api_turns=max(1, api_turns),
            trajectory=list(trajectory),
        )
        result.truncated = True
        return result
    api_turns += 1
    final_choice = response.choices[0] if getattr(response, "choices", None) else None
    final_msg = getattr(final_choice, "message", None) if final_choice else None
    trajectory.append(
        {
            "type": "assistant",
            "turn": max_tool_turns + 1,
            "role": role,
            "content": _extract_content(final_msg) if final_msg is not None else "",
            "reasoning": _extract_reasoning(final_msg) if final_msg is not None else "",
            "finish_reason": getattr(final_choice, "finish_reason", "") or "",
            "tool_calls": [],
            "is_final_answer_after_bound": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    result = _finalize(response)
    result.truncated = True
    return result
