"""Tool-calling loop for the Responses API dialect (Meta/muse-spark on 9router).

Why a SEPARATE loop rather than a branch inside ``providers/tool_loop.py``: the
wire protocol differs at every step, so a single function would be a pile of
branches with no shared code left in the middle.

  * tools are FLAT ({"type": "function", "name": ...}), not nested under
    "function";
  * a tool result goes back as an ITEM
    ({"type": "function_call_output", "call_id": ...}), not a role:"tool"
    message;
  * the answer is ``response.output_text`` / "message" items in
    ``response.output``, not ``choices[0].message``;
  * the model's completion state is ``response.status`` ("completed"), not
    ``choices[0].finish_reason``.

Everything provider-AGNOSTIC is IMPORTED from ``providers.tool_loop`` instead of
copied, so the two loops cannot drift:

  * ``_truncate_tool_output``      -- head+tail capping of tool results;
  * ``_cost_so_far``               -- the runaway-guard cost estimate;
  * ``_call_with_stall_guard``     -- no-progress bound around ONE HTTP call;
  * ``_is_context_overflow``       -- terminal vs transient failure;
  * ``_wrap_up_nudge``             -- the role-matched budget nudge (the
    read-only-role fix lives there, in one place);
  * ``_accumulate_response_usage`` -- per-response token/cache accounting;
  * ``call_with_retry``            -- retry THIS request, never the loop.

Behaviour that MUST match ``run_tool_loop``, and does:

  * Retry is PER REQUEST, never around the loop. Wrapping the loop restarts the
    conversation and hands out a fresh tool-turn budget -- measured on
    EXP-20260928-001: 99 calls against a budget of 60.
  * ``max_tool_turns`` and ``max_cost_usd`` are hard bounds. A stopped act asks
    for a final answer WITHOUT tools, is marked ``truncated``, and records its
    ``stop_reason`` inside the trajectory.
  * Tools are executed locally via ``agents.tools.execute_tool`` after
    ``reset_test_guard()`` / ``set_repo_root()``, results are truncated with
    ``_truncate_tool_output``, and every call lands in ``result.tool_calls``
    (artifact trail) and in the full ``trajectory``.
  * ``InferenceResult`` carries response, finish_reason, tool_calls,
    usage/tokens, model and elapsed.

  * AN EMPTY RESPONSE IS A FAILURE. Measured against 9router (2026-10-09): the
    muse-spark route answers ``chat.completions`` with HTTP 200,
    ``finish_reason="in_progress"``, empty content and ``total_tokens=0`` -- a
    complete-looking response containing nothing. Returned as-is it would be
    recorded as a successful act whose answer is "", so 150 sweep runs would
    fail with no error anywhere. Here such a response is retried in place and,
    if the retries are exhausted, raises :class:`EmptyResponseError` so the run
    fails loudly and ``--resume`` can pick it up.
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
    build_responses_inference_result,
    create_response_with_headers,
    extract_responses_reasoning,
    extract_responses_text,
    extract_responses_tool_calls,
    flatten_tool_schemas,
    responses_finish_reason,
    responses_response_is_empty,
)
from providers.system_prompts import TOOL_SYSTEM_PROMPT
from providers.tool_loop import (
    EmptyResponseError,
    StalledError,
    _accumulate_response_usage,
    _call_with_stall_guard,
    _cost_so_far,
    _is_context_overflow,
    _truncate_tool_output,
    _wrap_up_nudge,
)
from agents.tools import (
    TOOL_SCHEMAS,
    execute_tool,
    reset_test_guard,
    set_repo_root,
)


def _retryable_responses_response(resp) -> bool:
    """True when a single RESPONSES-API response would end the loop with nothing.

    Dialect note: this reads ``status`` and ``output`` items -- the Responses
    shape. The Chat Completions dialect has its own predicate
    (``tool_loop._retryable_chat_response``), which reads
    ``choices[0].message``; the two must not be mixed.

    A response WITH tool calls is normal even when its text is empty -- that is
    how tool calling looks. A response whose status is "in_progress" is never
    usable: the server itself says it did not finish, even if some text slipped
    through. Everything else is judged by the shared emptiness check.
    """
    if (getattr(resp, "status", "") or "") == "in_progress":
        return True
    return responses_response_is_empty(resp)


def _trim_input_items_for_context(input_items: list[dict], keep_chars: int) -> None:
    """Shrink the oldest tool outputs in place so one more request can fit.

    The Responses-dialect mirror of ``tool_loop._trim_messages_for_context``:
    only called when a request was ALREADY rejected for context length, so the
    goal is narrow -- make the next request fit without discarding the recent
    evidence the agent needs to answer. The most recent tool results are kept
    intact and the oldest are cut to a short head, because a decision about
    what to do next depends on what was just read, not on the first file
    opened.
    """
    indexes = [
        i
        for i, item in enumerate(input_items)
        if item.get("type") == "function_call_output"
    ]
    if not indexes:
        return

    # Keep the last few results whole; shrink everything before them.
    protected = set(indexes[-3:])
    for idx in indexes:
        if idx in protected:
            continue
        content = input_items[idx].get("output") or ""
        if len(content) <= keep_chars:
            continue
        input_items[idx]["output"] = (
            content[: keep_chars // 2]
            + "\n… [trimmed to fit the model's context limit] …\n"
        )


def run_tool_loop_responses(
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
    max_output_tokens: Optional[int] = None,
    reasoning: Optional[dict] = None,
    system_prompt: str = TOOL_SYSTEM_PROMPT,
) -> InferenceResult:
    """Run a tool-calling conversation over the Responses API and return the
    final answer.

    ``result.response`` is the LAST assistant text (tool results excluded).
    ``result.tool_calls`` records ``{"name", "arguments", "result"}`` for every
    executed call, and ``result.api_turns`` counts the LOGICAL turns the loop
    used -- a request that times out and is retried still consumed one turn, so
    a retry can never enlarge the budget the strategy granted. Token usage, by
    contrast, counts every request actually paid for, retries included.

    ``max_cost_usd`` is the runaway guard, not a tuning knob: the caller passes
    what remains of the TASK budget and the loop stops BEFORE issuing a request
    that would exceed it, exactly like the Chat Completions loop. It is priced
    with ``Config.COST_GUARD_MODEL`` when set (the rate the upstream actually
    bills), falling back to the run's own model.

    ``reasoning`` is passed straight through to the API (e.g.
    ``{"effort": "medium"}``); ``None`` sends nothing, because some endpoints
    reject an explicit disable with HTTP 400.
    """
    tools = flatten_tool_schemas(tools or TOOL_SCHEMAS)
    max_tool_turns = max_tool_turns or Config.MAX_TOOL_TURNS
    # Default the wall-clock bound from config so every provider inherits it
    # without each having to thread a new parameter through. Explicit argument
    # wins; 0 disables.
    if max_wall_seconds is None:
        configured = getattr(Config, "ACT_TIMEOUT_SECONDS", 0) or 0
        max_wall_seconds = configured if configured > 0 else None
    # No-progress bound, independent of the wall-clock bound above.
    stall_seconds = getattr(Config, "STALL_TIMEOUT_SECONDS", 0) or 0
    temperature = Config.TEMPERATURE if temperature is None else temperature
    timeout = Config.API_TIMEOUT if timeout is None else timeout
    max_output_tokens = (
        Config.MAX_TOKENS if max_output_tokens is None else max_output_tokens
    )
    output_limit = Config.TOOL_OUTPUT_MAX_CHARS
    t0 = time.perf_counter()

    # Rate card for the cost guard. None when no cap was requested, so the
    # default (uncapped) path does no pricing work at all.
    #
    # `is not None`, not truthiness: an EXHAUSTED cap arrives as 0.0 and must
    # still arm the guard. Testing truthiness would read "budget spent" as "no
    # budget", so the run would keep spending precisely when it should stop.
    capped = max_cost_usd is not None
    guard_model = getattr(Config, "COST_GUARD_MODEL", "") or model
    cost_rates = PricingTable.rates_for(guard_model, "off_peak") if capped else None
    cost_exceeded = False
    wall_exceeded = False
    context_exceeded = False

    # Always sync the sandbox to THIS instance's repo root, and start the act
    # with a clean repeat-call history -- same contract as the Chat Completions
    # loop (see its comments for the measured reasons).
    set_repo_root(repo_root)
    reset_test_guard()

    # The Responses dialect's conversation is a flat list of ITEMS. The system
    # prompt travels separately, as ``instructions``.
    input_items: list[dict] = [{"role": "user", "content": prompt}]
    recorded_calls: list[dict] = []
    trajectory: list[dict] = []
    usage_totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        # Sticky: one hit anywhere in the act is enough to disqualify it from
        # strategy comparison, so these are never reset back to False.
        "semantic_cache_hit": False,
        "semantic_cache_cost_saved_usd": 0.0,
        "semantic_cache_hit_turns": 0,
    }
    api_turns = 0

    def _base_kwargs() -> dict:
        kwargs: dict = {
            "model": model,
            "input": input_items,
            "instructions": system_prompt,
            "temperature": temperature,
            "timeout": timeout,
            "max_output_tokens": max_output_tokens,
        }
        if reasoning:
            kwargs["reasoning"] = reasoning
        return kwargs

    def _create(kwargs: dict, label: str):
        """One request, retried in place on failure or an empty answer.

        The retry wraps this single call rather than the loop, so a timeout on
        turn N resends turn N: the conversation so far and the turn budget are
        both untouched.

        After the retries are exhausted, an answer that is STILL empty raises
        :class:`EmptyResponseError` instead of being returned. This is the
        silent-failure guard: ``call_with_retry`` deliberately returns (not
        raises) the last retryable result, and letting that reach the caller
        would record an empty answer as a successful act.
        """

        def _attempt():
            # The stall guard wraps ONLY the HTTP call, deliberately -- the
            # same reasoning as the Chat Completions loop: an abandoned thread
            # must never mutate ``usage_totals`` after the retry replaced it,
            # so every shared-state write stays on this thread.
            resp = _call_with_stall_guard(
                lambda: create_response_with_headers(client, **kwargs),
                stall_seconds,
                label,
            )
            _accumulate_response_usage(usage_totals, resp)
            return resp

        resp = call_with_retry(
            _attempt,
            retry_on=_retryable_responses_response,
            # A context overflow and a stall cannot be cured by resending, so
            # they stop the act at once instead of burning the backoff schedule.
            fatal_on=lambda exc: _is_context_overflow(exc)
            or isinstance(exc, StalledError),
            label=label,
        )
        if _retryable_responses_response(resp):
            usage = getattr(resp, "usage", None)
            total_tokens = getattr(usage, "total_tokens", None) if usage is not None else None
            raise EmptyResponseError(
                f"{label}: {model} returned an EMPTY response "
                f"(status={getattr(resp, 'status', '') or '?'!r}, "
                f"total_tokens={total_tokens}) after retries -- no text and no "
                f"tool call. This is the silent-failure shape; failing loudly "
                f"so the run can be resumed."
            )
        return resp

    def _finalize(resp) -> InferenceResult:
        elapsed = time.perf_counter() - t0
        result = build_responses_inference_result(
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
        # act's time bound. The turn and cost guards cannot bound duration --
        # with retries, one request may sit in backoff for minutes.
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
        # what we already spent.
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

        # Budget pressure: tell the model to wrap up as the budget runs low,
        # with the nudge matched to the role's mandate (shared with the Chat
        # Completions loop so the two cannot drift).
        remaining = max_tool_turns - turn
        if remaining <= 1:
            input_items.append({"role": "user", "content": _wrap_up_nudge(role)})

        try:
            response = _create(kwargs, f"tool_loop_responses[{role}] turn {turn}")
        except Exception as exc:  # noqa: BLE001
            # A context overflow is terminal, not transient: the conversation
            # only grows, so retrying the same request fails again and is
            # billed again. Stop the act and ask for a final answer from what
            # is already known, the same recovery the turn/cost/clock bounds
            # use.
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

        text = extract_responses_text(response)
        calls = extract_responses_tool_calls(response)
        finish = responses_finish_reason(response)

        # Record the assistant turn BEFORE branching on whether it called a
        # tool, so the turn that ENDS the act is in the trajectory too.
        trajectory.append(
            {
                "type": "assistant",
                "turn": turn,
                "role": role,
                "content": text,
                "reasoning": extract_responses_reasoning(response),
                "finish_reason": finish,
                "tool_calls": [
                    {
                        "id": c["call_id"],
                        "name": c["name"],
                        "arguments": c["arguments"],
                    }
                    for c in calls
                ],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        if not calls:
            return _finalize(response)

        # Replay the calls as input items, then their results. MINIMAL replay:
        # only function_call + function_call_output are sent back -- the
        # model's own message/reasoning items are dropped. Verified live
        # against 9router: a second tool call and a final answer both arrive
        # without them, and it keeps each request (which re-sends the whole
        # conversation) smaller.
        for c in calls:
            input_items.append(
                {
                    "type": "function_call",
                    "name": c["name"],
                    "arguments": c["arguments"],
                    "call_id": c["call_id"],
                }
            )

        for c in calls:
            name = c["name"]
            try:
                args = json.loads(c["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            tool_out = execute_tool(name, args, role=role)
            recorded_calls.append(
                {"name": name, "arguments": args, "result": tool_out[:2000]}
            )
            logger.info(f"[toolcall] role={role} tool={name} args={args}")
            # The trajectory keeps the FULL result; the summary above keeps
            # the 2000-char preview (same split as the Chat Completions loop).
            trajectory.append(
                {
                    "type": "tool",
                    "turn": turn,
                    "role": role,
                    "tool_call_id": c["call_id"],
                    "name": name,
                    "arguments": args,
                    "result": tool_out,
                    "result_chars": len(tool_out),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": c["call_id"],
                    "output": _truncate_tool_output(tool_out, output_limit),
                }
            )

    # Out of turns, out of money, or out of time: ask for a final answer
    # WITHOUT tools so the model cannot call another one and loop again. Either
    # way the act was CUT OFF by a bound rather than finishing on its own, so
    # all paths mark the result truncated.
    if wall_exceeded:
        stop_reason = "wall_clock"
        logger.warning(
            f"Tool loop stopped on wall clock for role={role} "
            f"(bound {max_wall_seconds:.0f}s, elapsed {time.perf_counter() - t0:.0f}s)"
        )
    elif context_exceeded:
        stop_reason = "context_limit"
    elif cost_exceeded:
        stop_reason = "cost"
        logger.warning(
            f"Tool loop stopped on cost for role={role} "
            f"(cap ${max_cost_usd}, spent ${_cost_so_far(usage_totals, cost_rates or {}):.4f})"
        )
    else:
        stop_reason = "max_tool_turns"
        logger.warning(f"Tool loop hit max_tool_turns={max_tool_turns} for role={role}")

    # Mark the cutoff in the trajectory itself, so "was cut off" is readable
    # without grepping a log.
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

    # A CONTEXT overflow needs the conversation trimmed first, or the
    # final-answer request fails identically -- and would raise out of the
    # loop, losing the whole act.
    if context_exceeded:
        _trim_input_items_for_context(input_items, output_limit)

    try:
        response = _create(
            _base_kwargs(), f"tool_loop_responses[{role}] final-answer"
        )
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
    trajectory.append(
        {
            "type": "assistant",
            "turn": max_tool_turns + 1,
            "role": role,
            "content": extract_responses_text(response),
            "reasoning": extract_responses_reasoning(response),
            "finish_reason": responses_finish_reason(response),
            "tool_calls": [],
            "is_final_answer_after_bound": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    result = _finalize(response)
    result.truncated = True
    return result
