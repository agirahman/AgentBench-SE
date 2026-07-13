from google import genai
from google.genai import types

from config import Config
from utils.logger import logger


class GeminiProvider:
    """
    Wrapper sederhana untuk Google Gemini.
    Seluruh strategi (Direct, Planner, Reviewer)
    akan menggunakan provider ini.
    """

    def __init__(self):
        if not Config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY tidak ditemukan pada file .env")

        self.client = genai.Client(api_key=Config.GEMINI_API_KEY)
        self.model = Config.GEMINI_MODEL

        logger.info(f"Gemini model : {self.model}")

    def generate(self, prompt: str) -> str:
        """
        Mengirim prompt ke Gemini dan mengembalikan response text.
        """

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=Config.TEMPERATURE,
                    http_options=types.HttpOptions(timeout=60000),
                ),
            )

            return response.text

        except Exception as e:
            logger.error(f"Generate Error : {e}")
            raise

    def health_check(self) -> bool:
        """
        Mengecek apakah Gemini API dapat diakses.
        """

        try:
            self.generate("Reply with only: OK")
            logger.success("Gemini Health Check Passed")
            return True

        except Exception as e:
            logger.error(f"Health Check Failed : {e}")
            return False