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
    """
    tools = tools or TOOL_SCHEMAS
    max_tool_turns = max_tool_turns or Config.MAX_TOOL_TURNS
    temperature = Config.TEMPERATURE if temperature is None else temperature
    timeout = Config.API_TIMEOUT if timeout is None else timeout
    max_tokens = Config.MAX_TOKENS if max_tokens is None else max_tokens
    output_limit = Config.TOOL_OUTPUT_MAX_CHARS
    t0 = time.perf_counter()

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

    # Out of turns: ask for a final answer WITHOUT tools so the model cannot
    # call another one and loop again.
    logger.warning(f"Tool loop hit max_tool_turns={max_tool_turns} for role={role}")
    response = _create(_base_kwargs(), f"tool_loop[{role}] final-answer")
    api_turns += 1
    return _finalize(response)
