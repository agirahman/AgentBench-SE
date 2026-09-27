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


class OpenRouterProvider:
    def __init__(self):
        if not Config.OPENROUTER_API_KEY:
            raise ValueError("OPENROUTER_API_KEY tidak ditemukan pada file .env")

        self.client = OpenAI(
            api_key=Config.OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
        )
        self.model = Config.OPENROUTER_MODEL

        logger.info(f"OpenRouter model : {self.model}")

    def _extra_body(self) -> dict:
        """Reasoning passthrough for OpenRouter.

        Previously this ALWAYS sent ``{"reasoning": {"enabled": false}}`` when
        reasoning was not requested. That is not universally valid: some
        endpoints reject it outright with HTTP 400 "Reasoning is mandatory for
        this endpoint and cannot be disabled" (observed with
        stealth/space-bunny-alpha). Sending an explicit "off" is therefore opt-in
        via OPENROUTER_DISABLE_REASONING, and the default is to omit the field
        and let the provider apply its own policy.

        Returning an empty dict means no ``extra_body`` is attached at all.
        """
        if Config.OPENROUTER_REASONING:
            return {"reasoning": {"effort": Config.OPENROUTER_REASONING_EFFORT}}
        if Config.OPENROUTER_DISABLE_REASONING:
            return {"reasoning": {"enabled": False}}
        return {}

    @with_retry()
    def generate(self, prompt: str, role: str = "") -> InferenceResult:
        t0 = time.perf_counter()
        try:
            kwargs: dict = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": NO_TOOL_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": Config.TEMPERATURE,
                "timeout": Config.API_TIMEOUT,
                "max_tokens": Config.MAX_TOKENS,
            }
            kwargs["extra_body"] = self._extra_body()
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
                    f"OpenRouter response truncated (finish_reason='length'). "
                    f"Tokens: {result.total_tokens}. Role: {role}"
                )

            return result

        except Exception as e:
            logger.error(f"OpenRouter Generate Error: {e}")
            raise

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
        """Run a tool-calling conversation via the shared pipeline loop.

        Enables the edit-then-diff mechanism: the agent edits real files and the
        patch is derived from ``git diff``, instead of the model re-typing a
        unified diff (which produced unappliable patches on EXP-20260824-005).

        Tool calling requires the agent to be pointed at this instance's
        checked-out repo, so ``repo_root`` is threaded through to the sandbox.
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
                extra_body=self._extra_body(),
                system_prompt=system_prompt or TOOL_SYSTEM_PROMPT,
            )
        except Exception as e:
            logger.error(f"OpenRouter Tool Generate Error: {e}")
            raise

    def health_check(self) -> bool:
        try:
            self.generate("Reply with only: OK")
            logger.success("OpenRouter Health Check Passed")
            return True

        except Exception as e:
            logger.error(f"OpenRouter Health Check Failed: {e}")
            return False
