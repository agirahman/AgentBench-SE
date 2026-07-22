import time

from openai import OpenAI

from config import Config
from utils.logger import logger
from models.inference import InferenceResult
from evaluation.retry import with_retry
from providers.response_utils import build_openai_inference_result


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

    @with_retry()
    def generate(self, prompt: str, role: str = "") -> InferenceResult:
        t0 = time.perf_counter()
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=Config.TEMPERATURE,
                timeout=60,
                max_tokens=4096,
            )

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

    def health_check(self) -> bool:
        try:
            self.generate("Reply with only: OK")
            logger.success("OpenRouter Health Check Passed")
            return True

        except Exception as e:
            logger.error(f"OpenRouter Health Check Failed: {e}")
            return False
