import time
from functools import wraps

from config import Config
from utils.logger import logger

# Markers that identify a provider rate-limit rejection (HTTP 429). Matched
# case-insensitively against the exception text, because the SDKs raise
# different types (openai.RateLimitError, groq, google.api_core, …) and the
# message is the only field common to all of them.
_RATE_LIMIT_MARKERS = (
    "429",
    "rate limit",
    "rate_limit",
    "too many requests",
    "quota exceeded",
    "usage limit",
    "resource_exhausted",
    "resource exhausted",
)


def is_rate_limit_error(exc: BaseException) -> bool:
    """True if ``exc`` looks like a provider rate-limit / quota rejection.

    Checks the structured status code first (most reliable), then falls back to
    message markers. A 429 is not a transient network blip: retrying after 2s
    cannot help, because the window is typically hours. It needs its own, much
    longer backoff — or the run should stop so ``--resume`` can continue later.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 429:
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


def _backoff_delay(
    exc: BaseException | None,
    attempt: int,
    *,
    base_delay: float,
    rate_limit_base_delay: float,
    rate_limit_max_delay: float,
) -> float:
    """Backoff for this attempt; rate-limit errors get the long schedule."""
    if exc is not None and is_rate_limit_error(exc):
        return min(rate_limit_base_delay * (2 ** (attempt - 1)), rate_limit_max_delay)
    return base_delay * (2 ** (attempt - 1))


def call_with_retry(
    func,
    *,
    max_retries: int = None,
    base_delay: float = 2.0,
    retry_on: callable = None,
    rate_limit_base_delay: float = None,
    rate_limit_max_delay: float = None,
    label: str = "call",
):
    """Call ``func`` once, retrying that SINGLE call on failure.

    ``with_retry`` decorates a whole function, which is wrong when the function
    is itself a conversation loop. ``generate_with_tools`` runs the tool-calling
    loop, so a decorator-level retry restarted the entire conversation when one
    HTTP request timed out: the exploration already gathered was discarded AND
    the tool-turn budget was handed out a second time. Measured on
    EXP-20260928-001 (django-11019, direct): 45 + 16 + 38 = 99 tool calls
    against a budget of 60, and 152 minutes for one strategy.

    Calling this INSIDE the loop keeps both the exploration and the budget
    intact — a timeout on turn N resends turn N only.

    Same schedule and ``retry_on`` semantics as ``with_retry``: a retryable
    result on the final attempt is returned (not raised) so the caller can
    still use it, while an exception is re-raised after the last attempt.
    """
    retries = max_retries if max_retries is not None else Config.MAX_RETRIES
    rl_base = (
        Config.RATE_LIMIT_BACKOFF_BASE if rate_limit_base_delay is None
        else rate_limit_base_delay
    )
    rl_max = (
        Config.RATE_LIMIT_BACKOFF_MAX if rate_limit_max_delay is None
        else rate_limit_max_delay
    )

    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            result = func()
            if retry_on is not None and retry_on(result):
                if attempt >= retries:
                    logger.warning(
                        f"{label} produced a retryable result on final attempt "
                        f"— giving up"
                    )
                    return result
                delay = _backoff_delay(
                    None,
                    attempt,
                    base_delay=base_delay,
                    rate_limit_base_delay=rl_base,
                    rate_limit_max_delay=rl_max,
                )
                logger.warning(
                    f"{label} attempt {attempt}/{retries} returned a retryable "
                    f"result — retrying in {delay:.1f}s"
                )
                time.sleep(delay)
                continue
            return result
        except Exception as e:
            last_exc = e
            if attempt >= retries:
                logger.error(f"{label} failed after {retries} attempts: {e}")
                raise
            delay = _backoff_delay(
                e,
                attempt,
                base_delay=base_delay,
                rate_limit_base_delay=rl_base,
                rate_limit_max_delay=rl_max,
            )
            if is_rate_limit_error(e):
                logger.warning(
                    f"{label} attempt {attempt}/{retries} hit a rate limit — "
                    f"backing off {delay:.1f}s (rate-limit schedule)"
                )
            else:
                logger.warning(
                    f"{label} attempt {attempt}/{retries} failed: {e} "
                    f"— retrying in {delay:.1f}s"
                )
            time.sleep(delay)
    if last_exc is not None:
        raise last_exc


def with_retry(
    max_retries: int = Config.MAX_RETRIES,
    base_delay: float = 2.0,
    retry_on: callable = None,
    rate_limit_base_delay: float = None,
    rate_limit_max_delay: float = None,
):
    """Decorator: retry a function on exception, and optionally on return value.

    ``retry_on`` is an optional callable ``(return_value) -> bool``. When it
    returns True the attempt is treated as failed and retried (e.g. a model
    response that was truncated via ``finish_reason == "length"`` or came back
    empty). This catches silent failures that would otherwise pass through as a
    "successful" HTTP 200.

    Backoff schedule (base_delay=2.0):
        attempt 1 -> 0s (immediate)
        attempt 2 -> 2s
        attempt 3 -> 4s
        attempt 4 -> 8s

    Rate-limit (HTTP 429) failures use a separate, much longer schedule
    (``rate_limit_base_delay``, default 60s, capped at ``rate_limit_max_delay``,
    default 300s). A 5-hour usage limit is not cured by a 2-second pause, and
    hammering it just burns the remaining quota.

    On the final failed attempt the exception is re-raised so the caller
    (runner) can record it into ``ExperimentResult.evaluation.error``.

    WARNING: never wrap a loop with this. Retrying a whole tool-calling
    conversation restarts the exploration and resets the tool-turn budget —
    that is exactly the EXP-20260928-001 bug. Use ``call_with_retry`` inside
    the loop instead (see ``providers/tool_loop.py``).
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            return call_with_retry(
                lambda: func(*args, **kwargs),
                max_retries=max_retries,
                base_delay=base_delay,
                retry_on=retry_on,
                rate_limit_base_delay=rate_limit_base_delay,
                rate_limit_max_delay=rate_limit_max_delay,
                label=func.__name__,
            )

        return wrapper

    return decorator
