import time
from functools import wraps

from config import Config
from utils.logger import logger


def with_retry(max_retries: int = None, base_delay: float = 2.0):
    """Decorator: retry a function on exception with exponential backoff.

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
                    return func(*args, **kwargs)
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
            raise last_exc

        return wrapper

    return decorator
