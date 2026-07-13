from openai import OpenAI

from config import Config
from utils.logger import logger


class GroqProvider:
    def __init__(self):
        if not Config.GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY tidak ditemukan pada file .env")

        self.client = OpenAI(
            api_key=Config.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )
        self.model = Config.GROQ_MODEL
        self.last_usage = None

        logger.info(f"Groq model : {self.model}")

    def generate(self, prompt: str) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": prompt},
                ],
                temperature=Config.TEMPERATURE,
                timeout=60,
            )

            self.last_usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"Groq Generate Error: {e}")
            raise

    def health_check(self) -> bool:
        try:
            self.generate("Reply with only: OK")
            logger.success("Groq Health Check Passed")
            return True

        except Exception as e:
            logger.error(f"Groq Health Check Failed: {e}")
            return False
