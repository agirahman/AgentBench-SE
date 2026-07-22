import os
from dotenv import load_dotenv

load_dotenv()


def _get_env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _get_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class Config:
    GEMINI_API_KEY = _get_env("GEMINI_API_KEY")
    GEMINI_MODEL = _get_env(
        "GEMINI_MODEL",
        "gemini-3.1-flash-lite",
    )

    GROQ_API_KEY = _get_env("GROQ_API_KEY")
    GROQ_MODEL = _get_env(
        "GROQ_MODEL",
        "openai/gpt-oss-120b",
    )

    OPENCODE_API_KEY = _get_env("OPENCODE_API_KEY")
    OPENCODE_MODEL = _get_env(
        "OPENCODE_MODEL",
        "deepseek-v4-flash",
    )

    OPENROUTER_API_KEY = _get_env("OPENROUTER_API_KEY")
    OPENROUTER_MODEL = _get_env(
        "OPENROUTER_MODEL",
        "tencent/hy3:free",
    )

    TEMPERATURE = _get_float_env("TEMPERATURE", 0.2)
    MAX_RETRIES = _get_int_env("MAX_RETRIES", 3)

    USD_IDR_RATE = _get_float_env("USD_IDR_RATE", 16500.0)