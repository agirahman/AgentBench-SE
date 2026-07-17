import time

from openai import OpenAI

from config import Config
from utils.logger import logger
from models.inference import InferenceResult
from evaluation.retry import with_retry


class OpenCodeProvider:
    def __init__(self):
        if not Config.OPENCODE_API_KEY:
            raise ValueError("OPENCODE_API_KEY tidak ditemukan pada file .env")

        self.client = OpenAI(
            api_key=Config.OPENCODE_API_KEY,
            base_url="https://opencode.ai/zen/v1",
        )
        self.model = Config.OPENCODE_MODEL

        logger.info(f"OpenCode model : {self.model}")

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
                response_format={"type": "json_object"},
            )

            elapsed = time.perf_counter() - t0
            usage = response.usage
            finish = response.choices[0].finish_reason or ""
            content = response.choices[0].message.content or ""
            prompt_t = usage.prompt_tokens if usage else 0
            comp_t = usage.completion_tokens if usage else 0
            total_t = usage.total_tokens if usage else 0

            if finish == "length":
                logger.warning(
                    f"OpenCode response truncated (finish_reason='length'). "
                    f"Tokens: {total_t}. Role: {role}"
                )

            return InferenceResult(
                role=role,
                response=content,
                usage={
                    "prompt_tokens": prompt_t,
                    "completion_tokens": comp_t,
                    "total_tokens": total_t,
                },
                execution_time=elapsed,
                finish_reason=finish,
                model=self.model,
            )

        except Exception as e:
            logger.error(f"OpenCode Generate Error: {e}")
            raise

    def health_check(self) -> bool:
        try:
            self.generate("Reply with only: {\"status\": \"ok\"}")
            logger.success("OpenCode Health Check Passed")
            return True

        except Exception as e:
            logger.error(f"OpenCode Health Check Failed: {e}")
            return False
