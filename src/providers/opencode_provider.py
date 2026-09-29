"""OpenCode provider (via the 9router OpenAI-compatible proxy).

9router (localhost:20128/v1) fronts many upstream providers behind one endpoint,
which is why this provider and ``commandcode`` share a base URL: they are two
routes through the same local aggregator, selected by the model prefix
(``oc/...`` for OpenCode's models, ``cmd/...`` for CommandCode's).

Supports both paths, mirroring CommandCodeProvider:
  * generate()            -> single-shot JSON output, no tools.
  * generate_with_tools() -> the shared tool-calling loop (assistant -> tool ->
                            assistant), so agents explore the checkout instead of
                            being handed a pre-selected source snapshot.
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


class OpenCodeProvider:
    """OpenCode models routed through 9router, with optional tool calling."""

    def __init__(self):
        if not Config.OPENCODE_API_KEY:
            raise ValueError("OPENCODE_API_KEY tidak ditemukan pada file .env")

        # 9router fronts every upstream provider; base URL is shared with the
        # commandcode route on purpose.
        self.client = OpenAI(
            api_key=Config.OPENCODE_API_KEY,
            base_url=Config.OPENCODE_BASE_URL,
        )
        self.model = Config.OPENCODE_MODEL

        logger.info(f"OpenCode model : {self.model} (base {Config.OPENCODE_BASE_URL})")

    def _extra_body(self) -> dict:
        """Reasoning passthrough, mirroring the DeepSeek/CommandCode providers.

        Only attached when DEEPSEEK_THINKING is on, so a thinking-vs-nothinking
        comparison stays real at the API level. Sent as an empty dict (no
        ``extra_body``) otherwise, because some endpoints reject an explicit
        "reasoning disabled" with HTTP 400.
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
        try:
            self.client.models.list()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"OpenCode health check failed (endpoint): {e}")
            return False

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "Reply with only: OK"}],
                max_tokens=16,
                timeout=60,
            )
            content = ""
            choices = getattr(response, "choices", None) or []
            if choices:
                msg = getattr(choices[0], "message", None)
                content = (getattr(msg, "content", "") or "") if msg else ""
            if not content.strip():
                logger.warning(
                    f"OpenCode health check: model {self.model} returned empty content"
                )
                return False
            logger.success(f"OpenCode Health Check Passed (model={self.model})")
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"OpenCode health check failed (model={self.model}): {e}")
            return False

    # ------------------------------------------------------------------
    # Single-shot (no tools).
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
                "messages": [
                    {"role": "system", "content": NO_TOOL_SYSTEM_PROMPT},
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
                logger.warning(f"OpenCode response truncated (length). Role: {role}")
            return result
        except Exception as e:
            logger.error(f"OpenCode Generate Error: {e}")
            raise

    # ------------------------------------------------------------------
    # Tool-calling loop (shared with CommandCode/OpenRouter).
    #
    # No @with_retry here on purpose: retrying this function restarts the whole
    # conversation when one request times out, which discards the exploration
    # and hands out a fresh tool-turn budget (EXP-20260928-001: 99 calls against
    # a budget of 60). Retry is per request, inside the loop.
    # ------------------------------------------------------------------
    def generate_with_tools(
        self,
        prompt: str,
        role: str = "",
        tools: Optional[list] = None,
        max_tool_turns: Optional[int] = None,
        max_cost_usd: Optional[float] = None,
        repo_root: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> InferenceResult:
        """Run a tool-calling conversation and return the final answer.

        Delegates to ``providers.tool_loop`` so this provider cannot drift from
        the other two. ``result.response`` is the last assistant text; every
        executed call lands in ``result.tool_calls`` for the artifact trail.
        """
        try:
            return run_tool_loop(
                self.client,
                model=self.model,
                prompt=prompt,
                role=role,
                tools=tools or TOOL_SCHEMAS,
                max_tool_turns=max_tool_turns,
                max_cost_usd=max_cost_usd,
                repo_root=repo_root,
                extra_body=self._extra_body() or None,
                system_prompt=system_prompt or TOOL_SYSTEM_PROMPT,
            )
        except Exception as e:
            logger.error(f"OpenCode Tool Generate Error: {e}")
            raise
