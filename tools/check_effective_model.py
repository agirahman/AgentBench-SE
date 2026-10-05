"""Which model will the sweep actually use?

`.env` holds the free testing model (`oc/space-bunny-free`); the sweep overrides it
to the paid one (`cbai/deepseek-v4.1-flash`) via `--set`. That override is applied
LAST in run_with_env.py, so it should win -- but "should" is what this checks, by
running the same env layering the sweep uses and printing what Config ends up with.

This matters more than it looks: RQ3 needs the PAID route to reconcile against the
9router bill. If the override silently lost to .env, the sweep would run on a free
model, cost $0, and the cost analysis would be measuring nothing -- while every
other check passed.

Usage:
    python tools/check_effective_model.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Mirrors the sweep's own --set for the model.
SWEEP_MODEL = "cbai/deepseek-v4.1-flash"


def read_model(env_overrides: dict[str, str]) -> str:
    """Resolve Config.OPENCODE_MODEL in a fresh process, like the sweep does."""
    code = (
        "import sys; sys.path.insert(0, 'src');"
        "from config import Config; print(Config.OPENCODE_MODEL)"
    )
    env = os.environ.copy()
    # run_with_env.py clears anything that also exists in .env first, so a stale
    # shell value cannot win. Reproduce that, then apply the override last.
    from dotenv import dotenv_values

    for key in dotenv_values(ROOT / ".env"):
        env.pop(key, None)
    env.update(env_overrides)
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, cwd=str(ROOT),
        capture_output=True, text=True,
    )
    return out.stdout.strip()


def main() -> int:
    print("=" * 78)
    print("  EFFECTIVE MODEL -- what the sweep will really call")
    print("=" * 78)

    from dotenv import dotenv_values

    dotenv_model = dotenv_values(ROOT / ".env").get("OPENCODE_MODEL", "")

    print(f"\n  .env OPENCODE_MODEL            : {dotenv_model}")
    print(f"  sweep --set OPENCODE_MODEL     : {SWEEP_MODEL}")

    from_env_alone = read_model({})
    from_sweep = read_model({"OPENCODE_MODEL": SWEEP_MODEL})

    print(f"\n  resolved WITHOUT the override  : {from_env_alone}")
    print(f"  resolved WITH the sweep's --set: {from_sweep}")

    print()
    ok = from_sweep == SWEEP_MODEL
    if ok:
        print(f"  OK -- the sweep calls {SWEEP_MODEL}.")
        print("  The override beats .env, so a run started via")
        print("  tools/run_final_sweep.py uses the PAID route that RQ3 needs.")
    else:
        print(f"  PROBLEM -- the sweep would call {from_sweep!r}, not {SWEEP_MODEL!r}.")
        print("  The override is losing to .env. RQ3 would measure a FREE model and")
        print("  every cost figure would be $0 while all other checks still passed.")

    # A direct main.py run is a different, legitimate configuration.
    print()
    print("  NOTE: running src/main.py directly (not via the sweep) uses "
          f"{from_env_alone!r}.")
    print("  That is the free testing route -- correct for smoke tests, wrong for RQ3.")
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
