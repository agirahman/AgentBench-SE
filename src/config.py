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
        "llama-3.3-70b-versatile",
    )

    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    DEEPSEEK_MODEL = os.getenv(
        "DEEPSEEK_MODEL",
        "deepseek-v4-flash",
    )
    
    OPENCODE_API_KEY = os.getenv("OPENCODE_API_KEY")
    OPENCODE_MODEL = os.getenv(
        "OPENCODE_MODEL",
        "deepseek-v4-flash",
    )
    
    TEMPERATURE = float(os.getenv("TEMPERATURE", 0.2))
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))

    USD_IDR_RATE = float(os.getenv("USD_IDR_RATE", 16500.0))