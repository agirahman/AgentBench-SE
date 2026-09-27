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

import time
from typing import Optional

from openai import OpenAI

from config import Config
from utils.logger import logger
from models.inference import InferenceResult
from evaluation.retry import with_retry
from providers.response_utils import build_openai_inference_result
from providers.tool_loop import run_tool_loop
from providers.system_prompts import TOOL_SYSTEM_PROMPT, NO_TOOL_SYSTEM_PROMPT
from agents.tools import TOOL_SCHEMAS


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
            # The system prompt must match the path actually taken. It previously
            # said "use the provided tools" here too, while no tools were sent —
            # so the model narrated tool use it could not perform and returned no
            # patch. That is a direct contributor to the NO_DIFF failures.
            system_content = NO_TOOL_SYSTEM_PROMPT
            kwargs: dict = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": prompt},
                ],
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
                response,
                role=role,
                model=self.model,
                elapsed=elapsed,
                response_headers=getattr(response, "response_headers", None),
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
        system_prompt: Optional[str] = None,
    ) -> InferenceResult:
        """Run a tool-calling conversation and return the final answer.

        Delegates to the shared loop in ``providers.tool_loop`` so this provider
        and OpenRouter cannot drift apart. The final ``result.response`` is the
        LAST assistant text (tool messages excluded); every executed call is in
        ``result.tool_calls`` for the artifact trail.
        """
        try:
            return run_tool_loop(
                self.client,
                model=self.model,
                prompt=prompt,
                role=role,
                tools=tools or TOOL_SCHEMAS,
                max_tool_turns=max_tool_turns,
                repo_root=repo_root,
                extra_body=self._extra_body() or None,
                system_prompt=system_prompt or TOOL_SYSTEM_PROMPT,
            )
        except Exception as e:
            logger.error(f"CommandCode Tool Generate Error: {e}")
            raise
