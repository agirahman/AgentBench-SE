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
from typing import Optional

from config import Config
from utils.logger import logger
from models.inference import InferenceResult
from evaluation.cost import PricingTable
from evaluation.retry import call_with_retry
from providers.response_utils import build_openai_inference_result, _extract_cached_tokens
from providers.system_prompts import TOOL_SYSTEM_PROMPT, EDITING_ROLES as _EDITING_ROLES
from agents.tools import TOOL_SCHEMAS, execute_tool, set_repo_root


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
    capped = max_cost_usd is not None
    cost_rates = PricingTable.rates_for(model, "off_peak") if capped else None
    cost_exceeded = False
    wall_exceeded = False

    # Always sync the sandbox to THIS instance's repo root. Passing None resets
    # it to the shared base — never silently reuse a previous instance's root.
    set_repo_root(repo_root)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    recorded_calls: list[dict] = []
    usage_totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
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
        """
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
            resp = client.chat.completions.create(**kwargs)
            _record_usage(resp)
            return resp

        return call_with_retry(
            _attempt,
            retry_on=_retryable_response,
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

        response = _create(kwargs, f"tool_loop[{role}] turn {turn}")
        api_turns += 1
        choice = response.choices[0]
        msg = choice.message

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
            tool_out = execute_tool(name, args)
            recorded_calls.append(
                {"name": name, "arguments": args, "result": tool_out[:2000]}
            )
            logger.info(f"[toolcall] role={role} tool={name} args={args}")
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
        logger.warning(
            f"Tool loop stopped on wall clock for role={role} "
            f"(bound {max_wall_seconds:.0f}s, elapsed {time.perf_counter() - t0:.0f}s)"
        )
    elif cost_exceeded:
        logger.warning(
            f"Tool loop stopped on cost for role={role} "
            f"(cap ${max_cost_usd}, spent ${_cost_so_far(usage_totals, cost_rates or {}):.4f})"
        )
    else:
        logger.warning(f"Tool loop hit max_tool_turns={max_tool_turns} for role={role}")
    response = _create(_base_kwargs(), f"tool_loop[{role}] final-answer")
    api_turns += 1
    result = _finalize(response)
    result.truncated = True
    return result
