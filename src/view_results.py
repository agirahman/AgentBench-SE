"""Shim: backward-compatible entry point.

Legacy invocation::

    python src/view_results.py <command> [options]
"""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agentbench.core.view_results import main, load_data, build_strategy_difficulty_summary  # noqa: E402

__all__ = ["main", "load_data", "build_strategy_difficulty_summary"]

if __name__ == "__main__":
    main()