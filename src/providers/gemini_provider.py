import time

from google import genai
from google.genai import types

from config import Config
from utils.logger import logger
from models.inference import InferenceResult
from evaluation.retry import with_retry


class GeminiProvider:

    def __init__(self):
        if not Config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY tidak ditemukan pada file .env")

        self.client = genai.Client(api_key=Config.GEMINI_API_KEY)
        self.model = Config.GEMINI_MODEL

        logger.info(f"Gemini model : {self.model}")

    @with_retry()
    def generate(self, prompt: str, role: str = "") -> InferenceResult:
        t0 = time.perf_counter()
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=Config.TEMPERATURE,
                    http_options=types.HttpOptions(timeout=60000),
                ),
            )

            elapsed = time.perf_counter() - t0
            meta = response.usage_metadata
            finish = ""
            if response.candidates:
                fr = response.candidates[0].finish_reason
                finish = fr.name if fr else ""
            content = response.text or ""

            return InferenceResult(
                role=role,
                response=content,
                usage={
                    "prompt_tokens": meta.prompt_token_count if meta else 0,
                    "completion_tokens": meta.candidates_token_count if meta else 0,
                    "total_tokens": meta.total_token_count if meta else 0,
                },
                execution_time=elapsed,
                finish_reason=finish,
                model=self.model,
            )

        except Exception as e:
            logger.error(f"Gemini Generate Error: {e}")
            raise

    def health_check(self) -> bool:
        try:
            self.generate("Reply with only: OK")
            logger.success("Gemini Health Check Passed")
            return True

        except Exception as e:
            logger.error(f"Gemini Health Check Failed: {e}")
            return False
