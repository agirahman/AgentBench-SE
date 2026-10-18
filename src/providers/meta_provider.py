"""Meta (muse-spark) provider over the Responses API, via the 9router proxy.

Same route as ``opencode_provider`` -- same base URL, same key, same local
aggregator (9router on localhost:20128/v1) -- but a DIFFERENT DIALECT. Measured
directly against 9router (2026-10-09):

  * ``client.chat.completions.create`` for ``oc/muse-spark-1.3-contributor-free``
    returns HTTP 200 with ``finish_reason="in_progress"``, ``content=""`` and
    ``usage.total_tokens=0`` -- an empty answer with NO error, i.e. a silent
    failure. Running the sweep on that path would have produced 150 empty runs.
  * ``client.responses.create`` completes: text, reasoning and tool calling all
    work.

The model does NOT appear in ``GET /v1/models`` (9router lists only part of its
catalogue), so ``health_check`` deliberately probes the model itself instead of
the listing -- a listing-based check would report a working model as missing.

Mirrors ``OpenCodeProvider``:
  * ``generate()``            -> single-shot, no tools.
  * ``generate_with_tools()`` -> the Responses-API tool loop
    (``providers.tool_loop_responses``), which shares its provider-agnostic
    helpers (truncation, cost guard, stall guard, per-request retry) with the
    Chat Completions loop.
"""

from __future__ import annotations

import time
from typing import Optional

from openai import OpenAI

from config import Config
from utils.logger import logger
from models.inference import InferenceResult
from evaluation.retry import with_retry
from providers.response_utils import (
    build_responses_inference_result,
    create_response_with_headers,
    extract_responses_text,
    flatten_tool_schemas,
)
from providers.tool_loop import EmptyResponseError
from providers.tool_loop_responses import run_tool_loop_responses
from providers.system_prompts import TOOL_SYSTEM_PROMPT, NO_TOOL_SYSTEM_PROMPT
from agents.tools import TOOL_SCHEMAS


class MetaProvider:
    """Meta muse-spark models over the Responses API, with optional tool calling."""

    def __init__(self):
        if not Config.OPENCODE_API_KEY:
            raise ValueError("OPENCODE_API_KEY tidak ditemukan pada file .env")

        # 9router fronts every upstream provider; base URL and key are shared
        # with the opencode route on purpose.
        self.client = OpenAI(
            api_key=Config.OPENCODE_API_KEY,
            base_url=Config.OPENCODE_BASE_URL,
        )
        self.model = Config.META_MODEL

        logger.info(f"Meta model : {self.model} (base {Config.OPENCODE_BASE_URL})")

    def _reasoning(self) -> Optional[dict]:
        """Reasoning passthrough, mirroring the OpenCode/DeepSeek providers.

        Only attached when DEEPSEEK_THINKING is on, so a thinking-vs-nothinking
        comparison stays real at the API level. Sent as nothing otherwise,
        because some endpoints reject an explicit "reasoning disabled" with
        HTTP 400.
        """
        if not Config.DEEPSEEK_THINKING:
            return None
        return {"effort": Config.DEEPSEEK_REASONING_EFFORT}

    # ------------------------------------------------------------------
    # Health check: the model itself must answer. The model listing is NOT
    # consulted: 9router does not list this model, yet calls to it succeed.
    # ------------------------------------------------------------------
    def health_check(self) -> bool:
        try:
            response = create_response_with_headers(
                self.client,
                model=self.model,
                input="Reply with only: OK",
                max_output_tokens=1024,
                timeout=60,
            )
            status = getattr(response, "status", "") or ""
            text = extract_responses_text(response)
            if status != "completed" or not text.strip():
                logger.warning(
                    f"Meta health check: model {self.model} returned "
                    f"status={status!r} with empty text"
                )
                return False
            logger.success(f"Meta Health Check Passed (model={self.model})")
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Meta health check failed (model={self.model}): {e}")
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
                "input": prompt,
                "instructions": NO_TOOL_SYSTEM_PROMPT,
                "temperature": Config.TEMPERATURE,
                "timeout": Config.API_TIMEOUT,
                "max_output_tokens": Config.MAX_TOKENS,
                "text": {"format": {"type": "json_object"}},
            }
            reasoning = self._reasoning()
            if reasoning:
                kwargs["reasoning"] = reasoning
            response = create_response_with_headers(self.client, **kwargs)

            elapsed = time.perf_counter() - t0
            result = build_responses_inference_result(
                response,
                role=role,
                model=self.model,
                elapsed=elapsed,
                response_headers=getattr(response, "response_headers", None),
            )
            # An empty answer is a FAILURE, not a result. The @with_retry above
            # already resends it, but when the retries are exhausted
            # call_with_retry RETURNS the last retryable result rather than
            # raising -- and returning it here would record an empty response
            # as a successful act (the silent-failure shape measured on this
            # route via chat.completions: HTTP 200, empty content, 0 tokens).
            if not result.response.strip():
                raise EmptyResponseError(
                    f"generate(): {self.model} returned an EMPTY response "
                    f"(status={getattr(response, 'status', '')!r}) after retries; "
                    f"failing loudly instead of recording an empty answer."
                )
            if result.finish_reason == "length":
                logger.warning(f"Meta response truncated (length). Role: {role}")
            return result
        except Exception as e:
            logger.error(f"Meta Generate Error: {e}")
            raise

    # ------------------------------------------------------------------
    # Tool-calling loop (Responses API dialect).
    #
    # No @with_retry here on purpose: retrying this function restarts the whole
    # conversation when one request times out, which discards the exploration
    # and hands out a fresh tool-turn budget (EXP-20260928-001: 99 calls
    # against a budget of 60). Retry is per request, inside the loop.
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

        Delegates to ``providers.tool_loop_responses`` so this provider shares
        the truncation, cost-guard, stall-guard and per-request retry behaviour
        with the Chat Completions loop. ``result.response`` is the last
        assistant text; every executed call lands in ``result.tool_calls`` for
        the artifact trail.
        """
        try:
            return run_tool_loop_responses(
                self.client,
                model=self.model,
                prompt=prompt,
                role=role,
                tools=flatten_tool_schemas(tools or TOOL_SCHEMAS),
                max_tool_turns=max_tool_turns,
                max_cost_usd=max_cost_usd,
                repo_root=repo_root,
                reasoning=self._reasoning(),
                system_prompt=system_prompt or TOOL_SYSTEM_PROMPT,
            )
        except Exception as e:
            logger.error(f"Meta Tool Generate Error: {e}")
            raise
