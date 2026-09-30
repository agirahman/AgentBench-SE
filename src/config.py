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
    # 9router fronts many upstream providers behind one OpenAI-compatible
    # endpoint, so the opencode and commandcode routes share this base URL. The
    # model prefix selects the upstream (oc/... vs cmd/...).
    OPENCODE_BASE_URL = _get_env("OPENCODE_BASE_URL", "http://localhost:20128/v1")

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
    # Per-act cap. Only binds when TOTAL_TOOL_TURNS is disabled (0); otherwise
    # the strategy-wide pool below decides each act's share.
    MAX_TOOL_TURNS = _get_int_env("MAX_TOOL_TURNS", 8)
    # Strategy-wide tool-turn pool, so the three strategies get the same TOTAL
    # instead of a total that is an accident of how many agents they have.
    # See agents/budget.py. Set to 0 to fall back to the legacy per-act cap.
    #
    # The default is 60, not 200: 200 is a RUN DECISION (tools/run_final_sweep.py
    # sets it explicitly) sized from the reference implementations, while this
    # default keeps an ad-hoc invocation from silently spending 5x the tokens.
    TOTAL_TOOL_TURNS = _get_int_env("TOTAL_TOOL_TURNS", 60)
    # Turns review sets aside, out of its OWN task pool, for revision acts.
    # Carved out of TOTAL_TOOL_TURNS rather than added to it, so every strategy's
    # task still costs the same: with total=200 and this=32, direct and planning
    # get 200 and review gets 168 base + 32 revision = 200. That equality is what
    # makes the comparison fair; an earlier version added the reserve on top,
    # giving review a larger budget and confounding any claim that review is better.
    #
    # Sized from measurement, not taste. EXP-20260928-003 showed a revision with 1
    # turn cannot edit (django-11001: the reviewer diagnosed re.DOTALL correctly
    # and the fix could not be applied). The 15-run pilot then showed 4 turns is
    # still not enough -- the revision act spent all four reading and made 0 edits,
    # while acts that DID edit used 6-17 turns. 32 = 4 rounds x 8 turns, so each
    # revision and re-review gets 8.
    #
    # 0 = legacy behaviour, where revisions draw the base remainder and get a floor
    # of 1 turn -- and the review arm cannot really revise.
    REVISION_TOOL_TURNS = _get_int_env("REVISION_TOOL_TURNS", 0)
    # How the strategy-wide pool is handed out. "per_act" (default) splits the
    # remainder evenly at each act, which caps the first act and can leave a
    # later act on the floor of 1 turn. "per_task" gives each act the remainder
    # minus a floor held for the acts still to come -- the structure every
    # reference implementation uses (mini-SWE-agent, SWE-agent, OpenHands,
    # SWE-bench Pro all bound per task, never per agent). See agents/budget.py.
    BUDGET_MODE = _get_env("BUDGET_MODE", "per_act").strip().lower()
    # Turns guaranteed to each act still to come, in per_task mode. Only used
    # when BUDGET_MODE=per_task. 8-10 is the defensible band: enough for a
    # read-only act (reviewer) to read the plan and the patch and return a
    # verdict, while freeing the executor from the even-split cap.
    BUDGET_FLOOR_PER_ACT = _get_int_env("BUDGET_FLOOR_PER_ACT", 0)
    # Wall-clock bound for a single act, in seconds. The turn and cost guards do
    # not bound DURATION: a request that hits a rate limit sleeps
    # RATE_LIMIT_BACKOFF_BASE * 2^(n-1) capped at RATE_LIMIT_BACKOFF_MAX before
    # retrying, so a 40-turn act could spend over an hour in backoff alone.
    # Measured: one review run took 5,992 s (100 min) at pool 40, and 150 runs of
    # that shape is hours of pure waiting. The rate-limit breaker does not catch it
    # because the backoff happens inside the tool loop and never surfaces as a
    # failure to the runner. 0 = unbounded (the previous behaviour).
    ACT_TIMEOUT_SECONDS = _get_int_env("ACT_TIMEOUT_SECONDS", 1800)
    # Price every run with this model's rate card, whatever model actually served
    # it. Needed because the budget-curve runs use a free testing model whose real
    # rate is $0: without this, all 21 cost columns read 0.00, a dollar cap can
    # never bind, and RQ3 has no data (the state EXP-20260928-003 was left in).
    # The resulting figures are an ESTIMATE, and the pricing_version column is
    # suffixed so no reader mistakes them for a real charge. Empty = use the
    # model's own card.
    PRICING_MODEL_OVERRIDE = _get_env("PRICING_MODEL_OVERRIDE", "")
    # Runaway guard, in dollars, for the whole task (not per act) -- the same
    # shape the reference implementations use ($3 per task). Measured on
    # EXP-20260928-003, our runs cost ~$0.07/run worst case, so this does not bind
    # in normal operation; it exists so a pathological loop cannot spend without
    # bound. 0 = disabled.
    COST_LIMIT_USD = _get_float_env("COST_LIMIT_USD", 3.0)
    # Cap on a single tool result before it enters the conversation. Every turn
    # re-sends the whole conversation, so this value multiplies by the number of
    # turns. Head and tail are both kept (see providers/tool_loop): the tail
    # carries the error or the changed-file list.
    TOOL_OUTPUT_MAX_CHARS = _get_int_env("TOOL_OUTPUT_MAX_CHARS", 2000)
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


def _warn_if_source_context_enabled() -> None:
    """Warn that SOURCE_CONTEXT_ENABLED no longer changes the agent prompt.

    Issue.to_agent_prompt() now always returns the bare problem statement: the
    agent gathers evidence with tools, and a pre-injected snapshot would be a
    confound. The flag is kept only so an ablation baseline can be built on top
    of source_context.build_source_context. Leaving it silently ignored would let
    a researcher believe they had enabled (or disabled) passive context when the
    prompt was identical either way.
    """
    if not Config.SOURCE_CONTEXT_ENABLED:
        return
    try:
        from utils.logger import logger
    except Exception:  # noqa: BLE001
        return
    logger.warning(
        "SOURCE_CONTEXT_ENABLED=true has NO EFFECT: agents now gather evidence with "
        "tools, and Issue.to_agent_prompt() never injects a source snapshot. Set it "
        "to false to avoid confusion, or build the ablation explicitly."
    )


_warn_if_source_context_enabled()