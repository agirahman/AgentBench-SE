"""Shim: backward-compatible entry point.

Previously this repo ran experiments via ``python src/main.py``. The core
package now lives at ``agentbench/core/``. This thin shim re-exports the CLI
entry so legacy invocation keeps working:

    python src/main.py --provider openrouter --issues 5
"""

import os
import sys

# Make the package importable when run as a script from the repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agentbench.core.main import (  # noqa: E402
    parse_args,
    main,
)

__all__ = ["parse_args", "main"]

if __name__ == "__main__":
    main()