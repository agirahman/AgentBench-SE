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


def _sweep_routes() -> dict[str, dict[str, str]]:
    """Load the sweep's ROUTES dict -- the single source of truth.

    Importing the module (rather than copying its values here) is deliberate:
    a copied model string is exactly the drift this tool exists to detect.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sweep_for_model_check", ROOT / "tools" / "run_final_sweep.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ROUTES


def read_model(env_overrides: dict[str, str], attr: str) -> str:
    """Resolve Config.<attr> in a fresh process, like the sweep does."""
    code = (
        "import sys; sys.path.insert(0, 'src');"
        f"from config import Config; print(Config.{attr})"
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


#: Which Config attribute each route's env_key feeds. A wrong mapping here would
#: make the check verify the WRONG variable and pass while the sweep calls
#: something else, so the map is explicit rather than derived by string surgery.
_ENV_KEY_TO_ATTR = {
    "META_MODEL": "META_MODEL",
    "OPENCODE_MODEL": "OPENCODE_MODEL",
}


def check_route(route_name: str, route: dict[str, str]) -> bool:
    from dotenv import dotenv_values

    env_key = route["env_key"]
    model = route["model"]
    attr = _ENV_KEY_TO_ATTR.get(env_key)
    if attr is None:
        print(f"  PROBLEM -- no Config attribute known for env key {env_key!r}.")
        return False

    dotenv_model = dotenv_values(ROOT / ".env").get(env_key, "")

    print(f"\n  route                          : {route_name}")
    print(f"  sweep --provider               : {route['provider']}")
    print(f"  sweep --set {env_key:<19}: {model}")
    print(f"  .env {env_key:<24}: {dotenv_model or '(not set; Config default)'}")

    from_env_alone = read_model({}, attr)
    from_sweep = read_model({env_key: model}, attr)

    print(f"  resolved WITHOUT the override  : {from_env_alone}")
    print(f"  resolved WITH the sweep's --set: {from_sweep}")

    ok = from_sweep == model
    if ok:
        print(f"  OK -- the sweep calls {model} (Config.{attr}).")
        print("  The override beats .env, so a run started via")
        print("  tools/run_final_sweep.py uses the model the manifest records.")
    else:
        print(f"  PROBLEM -- the sweep would call {from_sweep!r}, not {model!r}.")
        print("  The override is losing to .env, or it sets the WRONG variable for")
        print("  the selected provider -- the run would use a different model than")
        print("  every other check assumes.")
    return ok


def main() -> int:
    print("=" * 78)
    print("  EFFECTIVE MODEL -- what the sweep will really call")
    print("=" * 78)

    routes = _sweep_routes()
    all_ok = True
    for name in sorted(routes):
        all_ok &= check_route(name, routes[name])

    # A direct main.py run is a different, legitimate configuration.
    print()
    print("  NOTE: running src/main.py directly (not via the sweep) uses the .env")
    print("  model. That is the .env default -- correct for smoke tests, wrong for")
    print("  the sweep.")
    print("=" * 78)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
