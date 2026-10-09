"""Which model will the sweep actually use?

The sweep overrides the model via `--set` (tools/run_final_sweep.py:build_cmd).
That override is applied LAST in run_with_env.py, so it should win over .env --
but "should" is what this checks, by running the same env layering the sweep
uses and printing what Config ends up with.

This matters more than it looks. The sweep route changed on 2026-10-09 to the
Meta muse-spark model over the RESPONSES API (`--provider meta`), whose model
variable is META_MODEL -- not OPENCODE_MODEL, which the previous paid route
used. If the override key and the provider's variable disagree, the sweep runs
.env's model while every other check passes: for the paid route that meant RQ3
measured a free model and every cost figure was $0. The check must therefore
name the SAME variable the provider reads (Config.META_MODEL), or it is
verifying nothing.

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

# Mirrors the sweep's own --set for the model (run_final_sweep.MODEL) and the
# provider it selects.
SWEEP_MODEL = "oc/muse-spark-1.3-contributor-free"
SWEEP_PROVIDER = "meta"


def read_model(env_overrides: dict[str, str]) -> str:
    """Resolve Config.META_MODEL in a fresh process, like the sweep does."""
    code = (
        "import sys; sys.path.insert(0, 'src');"
        "from config import Config; print(Config.META_MODEL)"
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

    dotenv_model = dotenv_values(ROOT / ".env").get("META_MODEL", "")

    print(f"\n  .env META_MODEL                : {dotenv_model or '(not set; Config default)'}")
    print(f"  sweep --provider               : {SWEEP_PROVIDER}")
    print(f"  sweep --set META_MODEL         : {SWEEP_MODEL}")

    from_env_alone = read_model({})
    from_sweep = read_model({"META_MODEL": SWEEP_MODEL})

    print(f"\n  resolved WITHOUT the override  : {from_env_alone}")
    print(f"  resolved WITH the sweep's --set: {from_sweep}")

    print()
    ok = from_sweep == SWEEP_MODEL
    if ok:
        print(f"  OK -- the sweep calls {SWEEP_MODEL}.")
        print("  The override beats .env, so a run started via")
        print("  tools/run_final_sweep.py uses the model the manifest records.")
    else:
        print(f"  PROBLEM -- the sweep would call {from_sweep!r}, not {SWEEP_MODEL!r}.")
        print("  The override is losing to .env, or it sets the WRONG variable for")
        print("  the selected provider -- the run would use a different model than")
        print("  every other check assumes.")

    # A direct main.py run is a different, legitimate configuration.
    print()
    print("  NOTE: running src/main.py directly (not via the sweep) uses "
          f"{from_env_alone!r}.")
    print("  That is the .env default -- correct for smoke tests, wrong for the sweep.")
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
