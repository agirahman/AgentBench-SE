import time
from typing import Optional

from openai import OpenAI

from config import Config
from utils.logger import logger
from models.inference import InferenceResult
from evaluation.retry import with_retry
from providers.response_utils import build_openai_inference_result
from providers.tool_loop import run_tool_loop
from providers.system_prompts import TOOL_SYSTEM_PROMPT
from agents.tools import TOOL_SCHEMAS


class DeepSeekProvider:
    """Provider resmi DeepSeek (API langsung via platform.deepseek.com)."""

    def __init__(self):
        if not Config.DEEPSEEK_API_KEY:
            raise ValueError("DEEPSEEK_API_KEY tidak ditemukan pada file .env")

        self.client = OpenAI(
            api_key=Config.DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com/v1",
        )
        self.model = Config.DEEPSEEK_MODEL
        self.user_id = ""

        logger.info(f"DeepSeek model : {self.model}")

    def _extra_body(self) -> dict:
        """Reasoning/thinking passthrough, shared by both call paths.

        Attached only when DEEPSEEK_THINKING is enabled, so the
        thinking-vs-nothinking comparison is real at the API level. The
        ``user_id`` (when set) rides along here too, so the single-shot and
        tool-calling paths cannot drift apart.
        """
        extra: dict = {}
        if Config.DEEPSEEK_THINKING:
            extra["thinking"] = {"type": "enabled"}
            extra["reasoning_effort"] = Config.DEEPSEEK_REASONING_EFFORT
        if self.user_id:
            extra["user_id"] = self.user_id
        return extra

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
            extra_body = self._extra_body()
            if extra_body:
                kwargs["extra_body"] = extra_body
            response = self.client.chat.completions.create(**kwargs)

            elapsed = time.perf_counter() - t0
            result = build_openai_inference_result(
                response,
                role=role,
                model=self.model,
                elapsed=elapsed,
            )

            if result.finish_reason == "length":
                logger.warning(
                    f"DeepSeek response truncated (finish_reason='length'). "
                    f"Tokens: {result.total_tokens}. Role: {role}"
                )

            return result

        except Exception as e:
            logger.error(f"DeepSeek Generate Error: {e}")
            raise

    # ------------------------------------------------------------------
    # Tool-calling loop.
    #
    # No @with_retry here on purpose: retrying this function restarts the whole
    # conversation when one request times out, which discards the exploration
    # and hands out a fresh tool-turn budget. Retry is per request, inside the
    # loop.
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

        Delegates to the shared loop in ``providers.tool_loop`` so this provider
        and the others cannot drift apart. The final ``result.response`` is the
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
                max_cost_usd=max_cost_usd,
                repo_root=repo_root,
                extra_body=self._extra_body() or None,
                system_prompt=system_prompt or TOOL_SYSTEM_PROMPT,
            )
        except Exception as e:
            logger.error(f"DeepSeek Tool Generate Error: {e}")
            raise

    def health_check(self) -> bool:
        try:
            self.generate("Reply with only: OK")
            logger.success("DeepSeek Health Check Passed")
            return True

        except Exception as e:
            logger.error(f"DeepSeek Health Check Failed: {e}")
            return False
