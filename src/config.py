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

    DEEPSEEK_API_KEY = _get_env("DEEPSEEK_API_KEY")
    DEEPSEEK_MODEL = _get_env(
        "DEEPSEEK_MODEL",
        "deepseek-v4-flash",
    )

    TEMPERATURE = _get_float_env("TEMPERATURE", 0.2)
    MAX_RETRIES = _get_int_env("MAX_RETRIES", 3)
    MAX_TOKENS = _get_int_env("MAX_TOKENS", 32768)
    API_TIMEOUT = _get_int_env("API_TIMEOUT", 180)
    MAX_REVISION_TURNS = _get_int_env("MAX_REVISION_TURNS", 1)

    OPENROUTER_REASONING = _get_env("OPENROUTER_REASONING", "false").lower() in ("1", "true", "yes")
    OPENROUTER_REASONING_EFFORT = _get_env("OPENROUTER_REASONING_EFFORT", "low")

    DEEPSEEK_THINKING = _get_env("DEEPSEEK_THINKING", "false").lower() in ("1", "true", "yes")
    DEEPSEEK_REASONING_EFFORT = _get_env("DEEPSEEK_REASONING_EFFORT", "low")

    SOURCE_CONTEXT_ENABLED = _get_env("SOURCE_CONTEXT_ENABLED", "true").lower() in ("1", "true", "yes")
    SOURCE_CONTEXT_MAX_CHARS = _get_int_env("SOURCE_CONTEXT_MAX_CHARS", 40000)
    SOURCE_CONTEXT_MAX_FILES = _get_int_env("SOURCE_CONTEXT_MAX_FILES", 12)
    SOURCE_CONTEXT_MAX_FILE_LINES = _get_int_env("SOURCE_CONTEXT_MAX_FILE_LINES", 600)
    REPO_CACHE_DIR = _get_env("REPO_CACHE_DIR", "datasets/repos")

    USD_IDR_RATE = _get_float_env("USD_IDR_RATE", 16500.0)