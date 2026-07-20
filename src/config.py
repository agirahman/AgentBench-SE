import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL = os.getenv(
        "GEMINI_MODEL",
        "gemini-3.1-flash-lite",
    )

    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    GROQ_MODEL = os.getenv(
        "GROQ_MODEL",
        "openai/gpt-oss-120b",
    )
    
    OPENCODE_API_KEY = os.getenv("OPENCODE_API_KEY")
    OPENCODE_MODEL = os.getenv(
        "OPENCODE_MODEL",
        "deepseek-v4-flash",
    )
    
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
    OPENROUTER_MODEL = os.getenv(
        "OPENROUTER_MODEL",
        "tencent/hy3:free",
    )
    
    TEMPERATURE = float(os.getenv("TEMPERATURE", 0.2))
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))

    USD_IDR_RATE = float(os.getenv("USD_IDR_RATE", 16500.0))