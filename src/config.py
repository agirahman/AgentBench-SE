import os
from pathlib import Path

from dotenv import load_dotenv, dotenv_values

# .env is this experiment's configuration file. ``load_dotenv()`` deliberately
# does NOT overwrite variables already present in the process environment — that
# is standard dotenv behaviour, but it means an ambient shell variable silently
# wins over the file the researcher edited.
#
# Measured on the development machine, the inherited environment carried
# OPENROUTER_MODEL=poolside/laguna-s-2.1:free and TOOLCALL_ENABLED=false while
# .env said stealth/space-bunny-alpha and true. A run would therefore have used a
# different model AND a different patch mechanism than experiment.yaml appeared
# to configure — a silent reproducibility break in a comparison study.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

# Keys whose value is a credential: never echo them, only report that they differ.
_SECRET_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD")


def _env_drift() -> dict[str, tuple[str, str]]:
    """Return {key: (env_value, dotenv_value)} for keys the shell overrides.

    Only keys actually defined in .env are considered, so unrelated ambient
    variables are ignored.
    """
    try:
        file_values = dotenv_values(_ENV_PATH)
    except Exception:  # noqa: BLE001 - never let diagnostics break startup
        return {}
    drift: dict[str, tuple[str, str]] = {}
    for key, file_value in file_values.items():
        if file_value is None:
            continue
        env_value = os.environ.get(key)
        if env_value is None:
            continue
        if env_value.strip() != file_value.strip():
            drift[key] = (env_value, file_value)
    return drift


def _report_env_drift() -> None:
    """Warn loudly when the ambient environment overrides .env."""
    drift = _env_drift()
    if not drift:
        return
    try:
        from utils.logger import logger
    except Exception:  # noqa: BLE001
        logger = None

    lines = [
        "=" * 72,
        "  CONFIG DRIFT: environment variables override .env",
        "=" * 72,
        "  The values below come from the SHELL, not from .env. load_dotenv() does",
        "  not overwrite variables that already exist, so the run will use these.",
    ]
    for key, (env_value, file_value) in sorted(drift.items()):
        if any(m in key.upper() for m in _SECRET_MARKERS):
            lines.append(f"    {key}: shell=<set, {len(env_value)} chars>  .env=<set, {len(file_value)} chars>")
        else:
            lines.append(f"    {key}: shell={env_value!r}  .env={file_value!r}")
    lines.append("  Unset the shell variables, or edit .env to match, before a real run.")
    lines.append("=" * 72)
    message = "\n".join(lines)

    if logger is not None:
        logger.warning(message)
    else:
        print(message, file=os.sys.stderr)


_report_env_drift()


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

    # Rate-limit (HTTP 429) handling. A usage-limit window is typically hours, so
    # retrying after the normal 2s backoff only burns remaining quota. These
    # control the separate, much longer backoff schedule used for 429s.
    RATE_LIMIT_BACKOFF_BASE = _get_float_env("RATE_LIMIT_BACKOFF_BASE", 60.0)
    RATE_LIMIT_BACKOFF_MAX = _get_float_env("RATE_LIMIT_BACKOFF_MAX", 300.0)
    # Stop the run after this many consecutive rate-limit failures so the user
    # can resume later, instead of grinding through the remaining quota. 0
    # disables the breaker (always keep retrying).
    RATE_LIMIT_CONSECUTIVE_LIMIT = _get_int_env("RATE_LIMIT_CONSECUTIVE_LIMIT", 5)
    MAX_TOKENS = _get_int_env("MAX_TOKENS", 32768)
    API_TIMEOUT = _get_int_env("API_TIMEOUT", 180)
    MAX_REVISION_TURNS = _get_int_env("MAX_REVISION_TURNS", 1)

    OPENROUTER_REASONING = _get_env("OPENROUTER_REASONING", "false").lower() in ("1", "true", "yes")
    OPENROUTER_REASONING_EFFORT = _get_env("OPENROUTER_REASONING_EFFORT", "low")
    # Opt-in explicit reasoning-off. Some OpenRouter endpoints reject
    # {"reasoning": {"enabled": false}} with HTTP 400 ("Reasoning is mandatory
    # for this endpoint and cannot be disabled"), so the default is to send
    # nothing and let the provider decide.
    OPENROUTER_DISABLE_REASONING = _get_env("OPENROUTER_DISABLE_REASONING", "false").lower() in ("1", "true", "yes")

    DEEPSEEK_THINKING = _get_env("DEEPSEEK_THINKING", "false").lower() in ("1", "true", "yes")
    DEEPSEEK_REASONING_EFFORT = _get_env("DEEPSEEK_REASONING_EFFORT", "low")

    COMMANDCODE_API_KEY = _get_env("COMMANDCODE_API_KEY")
    COMMANDCODE_BASE_URL = _get_env("COMMANDCODE_BASE_URL", "http://localhost:20128/v1")
    COMMANDCODE_MODEL = _get_env("COMMANDCODE_MODEL", "cmd/deepseek/deepseek-v4-flash")
    TOOLCALL_ENABLED = _get_env("TOOLCALL_ENABLED", "false").lower() in ("1", "true", "yes")
    MAX_TOOL_TURNS = _get_int_env("MAX_TOOL_TURNS", 8)
    TOOLCALL_REPO_DIR = _get_env("TOOLCALL_REPO_DIR", "datasets/repos")

    # Semantic patch applicability check (apply_status column). Costs one
    # `git apply --check` per produced patch against the cached repo; disable if
    # the repo cache is unavailable so a run is never blocked on it.
    APPLY_CHECK_ENABLED = _get_env("APPLY_CHECK_ENABLED", "true").lower() in ("1", "true", "yes")

    # When true, prompts are reordered so a long, identical static header sits at
    # the top (enabling automatic prefix caching within a strategy). When false,
    # the legacy layout is used so existing runs stay reproducible.
    PROMPT_CACHE_LAYOUT = _get_env("PROMPT_CACHE_LAYOUT", "false").lower() in ("1", "true", "yes")

    SOURCE_CONTEXT_ENABLED = _get_env("SOURCE_CONTEXT_ENABLED", "true").lower() in ("1", "true", "yes")
    SOURCE_CONTEXT_MAX_CHARS = _get_int_env("SOURCE_CONTEXT_MAX_CHARS", 40000)
    SOURCE_CONTEXT_MAX_FILES = _get_int_env("SOURCE_CONTEXT_MAX_FILES", 12)
    SOURCE_CONTEXT_MAX_FILE_LINES = _get_int_env("SOURCE_CONTEXT_MAX_FILE_LINES", 600)
    REPO_CACHE_DIR = _get_env("REPO_CACHE_DIR", "datasets/repos")

    USD_IDR_RATE = _get_float_env("USD_IDR_RATE", 16500.0)