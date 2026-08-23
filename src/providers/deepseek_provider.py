import time

from openai import OpenAI

from config import Config
from utils.logger import logger
from models.inference import InferenceResult
from evaluation.retry import with_retry
from providers.response_utils import build_openai_inference_result


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
            if Config.DEEPSEEK_THINKING:
                kwargs["extra_body"] = {
                    "thinking": {"type": "enabled"},
                    "reasoning_effort": Config.DEEPSEEK_REASONING_EFFORT,
                }
            if self.user_id:
                extra_body = kwargs.get("extra_body") or {}
                extra_body["user_id"] = self.user_id
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

    def health_check(self) -> bool:
        try:
            self.generate("Reply with only: OK")
            logger.success("DeepSeek Health Check Passed")
            return True

        except Exception as e:
            logger.error(f"DeepSeek Health Check Failed: {e}")
            return False
