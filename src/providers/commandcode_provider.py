"""CommandCode provider (via 9router OpenAI-compatible proxy).

Routes through 9router (localhost:20128/v1) -> CommandCode subscription, using
the deepseek-v4-flash model. Supports BOTH:
  * generate()            -> single-shot JSON output (no tools), for agents that
                            do not use tools. Kept identical to other providers so
                            the non-toolcall pipeline is untouched.
  * generate_with_tools() -> tool-calling loop (assistant -> tool -> assistant)
                            that lets agents actively explore the repo instead of
                            relying on passive SOURCE_CONTEXT injection.

Tool calling requires NOT sending response_format=json_object (DeepSeek / 9router
rejects json_object + tools together), so the tool path omits it.
"""

from __future__ import annotations

import json
import time
from typing import Optional

from openai import OpenAI

from config import Config
from utils.logger import logger
from models.inference import InferenceResult
from evaluation.retry import with_retry
from providers.response_utils import build_openai_inference_result, _extract_cached_tokens
from agents.tools import TOOL_SCHEMAS, execute_tool, set_repo_root


class CommandCodeProvider:
    """CommandCode (9router proxy) provider with optional tool calling."""

    def __init__(self):
        if not Config.COMMANDCODE_API_KEY:
            raise ValueError("COMMANDCODE_API_KEY tidak ditemukan pada file .env")
        self.client = OpenAI(
            api_key=Config.COMMANDCODE_API_KEY,
            base_url=Config.COMMANDCODE_BASE_URL,
        )
        self.model = Config.COMMANDCODE_MODEL
        self.user_id = ""
        logger.info(f"CommandCode model : {self.model} (base {Config.COMMANDCODE_BASE_URL})")

    def _extra_body(self) -> dict:
        """Reasoning/thinking passthrough, mirroring DeepSeek provider.

        Only attached when DEEPSEEK_THINKING is enabled so that the
        thinking-vs-nothinking comparison is real at the API level.
        """
        if not Config.DEEPSEEK_THINKING:
            return {}
        return {
            "thinking": {"type": "enabled"},
            "reasoning_effort": Config.DEEPSEEK_REASONING_EFFORT,
        }

    # ------------------------------------------------------------------
    # Health check: endpoint reachable AND the configured model responds.
    # ------------------------------------------------------------------
    def health_check(self) -> bool:
        # 1) Endpoint/key reachable via models list (cheap, no generation).
        try:
            self.client.models.list()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"CommandCode health check failed (endpoint): {e}")
            return False

        # 2) The configured model must actually answer a tiny prompt.
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "Reply with only: OK"}],
                max_tokens=16,
                timeout=30,
            )
            content = ""
            choices = getattr(response, "choices", None) or []
            if choices:
                msg = getattr(choices[0], "message", None)
                content = (getattr(msg, "content", "") or "") if msg else ""
            if not content.strip():
                logger.warning(
                    f"CommandCode health check: model {self.model} returned empty content"
                )
                return False
            logger.success(f"CommandCode Health Check Passed (model={self.model})")
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"CommandCode health check failed (model={self.model}): {e}")
            return False

    # ------------------------------------------------------------------
    # Single-shot (no tools) — identical behaviour to other providers.
    # ------------------------------------------------------------------
    @with_retry(
        retry_on=lambda r: (
            getattr(r, "finish_reason", "") == "length"
            or not getattr(r, "response", "").strip()
        )
    )
    def generate(self, prompt: str, role: str = "") -> InferenceResult:
        t0 = time.perf_counter()
        try:
            if "json" not in prompt.lower():
                prompt = f"{prompt}\n\nRespond in valid JSON."
            kwargs: dict = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": Config.TEMPERATURE,
                "timeout": Config.API_TIMEOUT,
                "max_tokens": Config.MAX_TOKENS,
                "response_format": {"type": "json_object"},
            }
            extra = self._extra_body()
            if extra:
                kwargs["extra_body"] = extra
            response = self.client.chat.completions.create(**kwargs)
            elapsed = time.perf_counter() - t0
            result = build_openai_inference_result(
                response, role=role, model=self.model, elapsed=elapsed
            )
            if result.finish_reason == "length":
                logger.warning(f"CommandCode response truncated (length). Role: {role}")
            return result
        except Exception as e:
            logger.error(f"CommandCode Generate Error: {e}")
            raise

    # ------------------------------------------------------------------
    # Tool-calling loop.
    # ------------------------------------------------------------------
    @with_retry(
        retry_on=lambda r: not getattr(r, "response", "").strip()
    )
    def generate_with_tools(
        self,
        prompt: str,
        role: str = "",
        tools: Optional[list] = None,
        max_tool_turns: Optional[int] = None,
        repo_root: Optional[str] = None,
    ) -> InferenceResult:
        """Run a tool-calling conversation and return the final answer.

        The final InferenceResult.response is the LAST assistant text (tool
        messages excluded). All tool calls are recorded in result.tool_calls as
        a list of {"name", "arguments", "result"} dicts for logging.

        If ``repo_root`` is given, tools explore that checked-out instance repo
        (e.g. datasets/repos/psf/requests/<hash>) so agents never guess paths.
        """
        tools = tools or TOOL_SCHEMAS
        max_tool_turns = max_tool_turns or Config.MAX_TOOL_TURNS
        t0 = time.perf_counter()

        # Always sync the tool sandbox to THIS instance's repo root. Passing
        # None resets it to the global sandbox base — never reuse a previous
        # instance's root silently.
        set_repo_root(repo_root)
        if repo_root:
            system_content = (
                "You are a software engineering agent. "
                f"All file paths are RELATIVE TO the repository root: {repo_root}\n"
                "Use the provided tools to explore that repository and gather evidence "
                "before producing your final answer. When you have enough information, "
                "respond with your final answer (no tool call)."
            )
        else:
            system_content = (
                "You are a software engineering agent. "
                "Use the provided tools to explore the repository and gather evidence "
                "before producing your final answer. When you have enough information, "
                "respond with your final answer (no tool call)."
            )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt},
        ]
        recorded_calls: list[dict] = []
        # Cumulative usage across ALL API turns: each turn re-sends the growing
        # conversation, so summing is required or token/cost metrics undercount
        # by 3-10x on tool-heavy runs.
        usage_totals = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
        }
        api_turns = 0

        def _accumulate_usage(resp) -> None:
            nonlocal api_turns
            api_turns += 1
            u = getattr(resp, "usage", None)
            if u is None:
                return
            pt = getattr(u, "prompt_tokens", 0) or 0
            ct = getattr(u, "completion_tokens", 0) or 0
            tt = getattr(u, "total_tokens", 0) or (pt + ct)
            ca = _extract_cached_tokens(u)
            usage_totals["prompt_tokens"] += pt
            usage_totals["completion_tokens"] += ct
            usage_totals["total_tokens"] += tt
            usage_totals["cached_tokens"] += ca

        def _finalize(resp) -> InferenceResult:
            elapsed = time.perf_counter() - t0
            result = build_openai_inference_result(
                resp, role=role, model=self.model, elapsed=elapsed
            )
            # Override per-call usage with cumulative loop totals.
            result.usage = dict(usage_totals)
            result.tool_calls = recorded_calls
            result.api_turns = max(1, api_turns)
            return result

        try:
            for _ in range(max_tool_turns):
                kwargs: dict = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": Config.TEMPERATURE,
                    "timeout": Config.API_TIMEOUT,
                    "max_tokens": Config.MAX_TOKENS,
                    "tools": tools,
                    "tool_choice": "auto",
                }
                extra = self._extra_body()
                if extra:
                    kwargs["extra_body"] = extra
                response = self.client.chat.completions.create(**kwargs)
                _accumulate_usage(response)
                choice = response.choices[0]
                msg = choice.message
                finish = getattr(choice, "finish_reason", "")

                # No tool call -> this is the final answer.
                if not getattr(msg, "tool_calls", None):
                    return _finalize(response)

                # Append assistant message (with tool_calls) to history.
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

                # Execute each tool call and feed results back.
                for tc in msg.tool_calls:
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
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
                            "content": tool_out[:8000],
                        }
                    )

            # Reached max turns without a final answer; force one last call.
            logger.warning(f"CommandCode tool loop hit max_tool_turns={max_tool_turns} for role={role}")
            kwargs_final = {
                "model": self.model,
                "messages": messages,
                "temperature": Config.TEMPERATURE,
                "timeout": Config.API_TIMEOUT,
                "max_tokens": Config.MAX_TOKENS,
            }
            response = self.client.chat.completions.create(**kwargs_final)
            _accumulate_usage(response)
            return _finalize(response)
        except Exception as e:
            logger.error(f"CommandCode Tool Generate Error: {e}")
            raise
