"""Run the experiment with .env as the single source of truth.

Why this exists: `load_dotenv()` does not override variables already present in
the environment, and this shell inherits stale values (OPENROUTER_MODEL,
TOOLCALL_ENABLED, OPENROUTER_API_KEY) from the parent Command Code process. A run
started here would silently use a different model and provider config than .env
says. This wrapper deletes every variable whose name appears in .env before
invoking main.py, so .env wins.

Explicit overrides go through ``--set KEY=VALUE``, applied AFTER the clearing so
they survive. This exists for sweeps that vary one knob across runs (the budget
curve varies TOTAL_TOOL_TURNS): editing .env between runs would leave the file
modified if a run crashed, and a crash mid-sweep would then make every later run
use the wrong budget with no record of it. Overrides are also printed and echoed
into the log, so a sweep's configuration is recoverable from its own output.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


def env_keys(path: Path) -> list[str]:
    keys = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.append(line.split("=", 1)[0].strip())
    return keys


def _split_overrides(argv: list[str]) -> tuple[dict[str, str], list[str]]:
    """Pull ``--set KEY=VALUE`` pairs out of argv, returning (overrides, rest).

    Parsed here rather than with argparse so the remaining arguments can be
    forwarded to main.py untouched, including its own flags.
    """
    overrides: dict[str, str] = {}
    rest: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--set":
            if i + 1 >= len(argv):
                print("--set needs KEY=VALUE", file=sys.stderr)
                raise SystemExit(2)
            key, _, value = argv[i + 1].partition("=")
            if not key or not _:
                print(f"--set expects KEY=VALUE, got {argv[i + 1]!r}", file=sys.stderr)
                raise SystemExit(2)
            overrides[key.strip()] = value
            i += 2
            continue
        rest.append(argv[i])
        i += 1
    return overrides, rest


def main() -> int:
    if not ENV_FILE.exists():
        print("no .env found", file=sys.stderr)
        return 1

    overrides, rest = _split_overrides(sys.argv[1:])

    cleared = []
    for k in env_keys(ENV_FILE):
        if os.environ.pop(k, None) is not None:
            cleared.append(k)

    print(f"[env] cleared {len(cleared)} stale variable(s) so .env wins: {', '.join(cleared)}")
    # Show the values that matter, without printing any secret.
    from dotenv import dotenv_values

    vals = dotenv_values(ENV_FILE)
    for k in ("PROVIDER", "OPENROUTER_MODEL", "TOOLCALL_ENABLED",
              "SOURCE_CONTEXT_ENABLED", "MAX_TOOL_TURNS", "APPLY_CHECK_ENABLED"):
        if k in vals:
            print(f"[env] {k} = {vals[k]}")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    # Applied last: these must beat both the ambient shell and .env.
    for k, v in overrides.items():
        env[k] = v
    if overrides:
        for k, v in sorted(overrides.items()):
            print(f"[env] OVERRIDE {k} = {v}")

    cmd = [sys.executable, str(ROOT / "src" / "main.py"), *rest]
    print(f"[run] {' '.join(cmd[1:])}")
    return subprocess.call(cmd, env=env, cwd=str(ROOT))


if __name__ == "__main__":
    sys.exit(main())
