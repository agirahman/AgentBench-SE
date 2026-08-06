"""Loguru logger setup + console-sink helper.

The default sink (id 0) writes to stderr — useful for the legacy CLI but
noisy inside the interactive shell's rich progress UI. ``silence_console``
removes the tracked stderr sink so only file sinks remain
(logs/agentbench.log + per-experiment log added by the runner);
``restore_console`` re-adds it, keeping the id tracked for later runs.
"""

import sys

from loguru import logger

logger.add(
    "logs/agentbench.log",
    rotation="5 MB",
    level="INFO",
)

stderr_sink_ids: set[int] = set()

CONSOLE_SINK_ID = 0  # loguru's default stderr sink


def _ensure_tracked() -> None:
    """Track the default stderr sink (present until first removed)."""
    stderr_sink_ids.add(CONSOLE_SINK_ID)


def silence_console() -> bool:
    """Remove the tracked stderr sink(s); return True if any were present."""
    _ensure_tracked()
    ids = list(stderr_sink_ids)
    removed = False
    for hid in ids:
        try:
            logger.remove(hid)
            removed = True
        except (ValueError, KeyError):
            pass
    stderr_sink_ids.clear()
    return removed


def restore_console() -> None:
    """Re-add a single stderr sink if none tracked exists."""
    if stderr_sink_ids:
        return
    hid = logger.add(sys.stderr, level="INFO", backtrace=False, diagnose=False)
    stderr_sink_ids.add(hid)


__all__ = ["logger", "silence_console", "restore_console"]