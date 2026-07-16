import time

from openai import OpenAI

from config import Config
from utils.logger import logger
from models.inference import InferenceResult
from evaluation.retry import with_retry

# DeepSeek V4 Flash pricing (per 1M tokens):
#   Input  (cache miss): $0.14
#   Input  (cache hit):  $0.0028
#   Output:              $0.28


class DeepSeekProvider:
    def __init__(self):
        if not Config.DEEPSEEK_API_KEY:
            raise ValueError("DEEPSEEK_API_KEY tidak ditemukan pada file .env")

        self.client = OpenAI(
            api_key=Config.DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com",
        )
        self.model = Config.DEEPSEEK_MODEL

        logger.info(f"DeepSeek model : {self.model}")

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
            usage = response.usage
            finish = response.choices[0].finish_reason or ""
            content = response.choices[0].message.content or ""
            prompt_t = usage.prompt_tokens if usage else 0
            comp_t = usage.completion_tokens if usage else 0
            total_t = usage.total_tokens if usage else 0

            if finish == "length":
                logger.warning(
                    f"DeepSeek response truncated (finish_reason='length'). "
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