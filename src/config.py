import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL = os.getenv(
        "GEMINI_MODEL",
        "gemini-3.5-flash",
    )

    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    GROQ_MODEL = os.getenv(
        "GROQ_MODEL",
        "llama-3.3-70b-versatile",
    )
    
    TEMPERATURE = float(os.getenv("TEMPERATURE", 0.2))
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))

    USD_IDR_RATE = float(os.getenv("USD_IDR_RATE", 16500.0))