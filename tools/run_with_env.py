"""Run the experiment with .env as the single source of truth.

Why this exists: `load_dotenv()` does not override variables already present in
the environment, and this shell inherits stale values (OPENROUTER_MODEL,
TOOLCALL_ENABLED, OPENROUTER_API_KEY) from the parent Command Code process. A run
started here would silently use a different model and provider config than .env
says. This wrapper deletes every variable whose name appears in .env before
invoking main.py, so .env wins.
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


def main() -> int:
    if not ENV_FILE.exists():
        print("no .env found", file=sys.stderr)
        return 1

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
    cmd = [sys.executable, str(ROOT / "src" / "main.py"), *sys.argv[1:]]
    print(f"[run] {' '.join(cmd[1:])}")
    return subprocess.call(cmd, env=env, cwd=str(ROOT))


if __name__ == "__main__":
    sys.exit(main())
