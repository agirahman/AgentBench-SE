#!/usr/bin/env python
"""Convenience launcher for `view_results` on an eval/results.csv.

Sets up sys.path so the `evaluation` package is importable, then delegates
to src.view_results.main with the given --file path and subcommand.

Usage:
    python tools/view_eval.py results/EXP-xxx/eval/results.csv
    python tools/view_eval.py results/EXP-xxx/eval/results.csv summary
    python tools/view_eval.py results/EXP-xxx/eval/results.csv significance
    python tools/view_eval.py results/EXP-xxx/eval/results.csv compare

Default subcommand is "significance" (paired McNemar + Wilson CI).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the src/ package importable regardless of CWD.
_HERE = Path(__file__).resolve().parent
_SRC = (_HERE / ".." / "src").resolve()
sys.path.insert(0, str(_SRC))

from view_results import main as _main  # noqa: E402


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    csv_path = args[0]
    sub = args[1] if len(args) > 1 else "significance"

    # The CSV must exist; resolve relative to CWD so users can pass a
    # path relative to the project root.
    resolved = Path(csv_path)
    if not resolved.exists():
        print(f"[error] file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    sys.argv = ["view_results", sub, "--file", str(resolved)]
    _main()


if __name__ == "__main__":
    main()
