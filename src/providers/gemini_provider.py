from google import genai
from google.genai import types

from config import Config
from utils.logger import logger


class GeminiProvider:

    def __init__(self):
        if not Config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY tidak ditemukan pada file .env")

        self.client = genai.Client(api_key=Config.GEMINI_API_KEY)
        self.model = Config.GEMINI_MODEL
        self.last_usage = None

        logger.info(f"Gemini model : {self.model}")

    def generate(self, prompt: str) -> str:
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=Config.TEMPERATURE,
                    http_options=types.HttpOptions(timeout=60000),
                ),
            )

            meta = response.usage_metadata
            self.last_usage = {
                "prompt_tokens": meta.prompt_token_count,
                "completion_tokens": meta.candidates_token_count,
                "total_tokens": meta.total_token_count,
            }

            return response.text

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