import time
from functools import wraps

from config import Config
from utils.logger import logger


def with_retry(
    max_retries: int = Config.MAX_RETRIES,
    base_delay: float = 2.0,
    retry_on: callable = None,
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

    On the final failed attempt the exception is re-raised so the caller
    (runner) can record it into ``ExperimentResult.evaluation.error``.
    """
    retries = max_retries if max_retries is not None else Config.MAX_RETRIES

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, retries + 1):
                try:
                    result = func(*args, **kwargs)
                    if retry_on is not None and retry_on(result):
                        if attempt >= retries:
                            logger.warning(
                                f"{func.__name__} produced a retryable result on "
                                f"final attempt ({retry_on(result)!r}) — giving up"
                            )
                            return result
                        delay = base_delay * (2 ** (attempt - 1))
                        logger.warning(
                            f"{func.__name__} attempt {attempt}/{retries} returned "
                            f"retryable result — retrying in {delay:.1f}s"
                        )
                        time.sleep(delay)
                        continue
                    return result
                except Exception as e:
                    last_exc = e
                    if attempt >= retries:
                        logger.error(
                            f"{func.__name__} failed after {retries} attempts: {e}"
                        )
                        raise
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        f"{func.__name__} attempt {attempt}/{retries} failed: {e} "
                        f"— retrying in {delay:.1f}s"
                    )
                    time.sleep(delay)
            if last_exc is not None:
                raise last_exc

        return wrapper

    return decorator
