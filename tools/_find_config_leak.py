"""Find WHICH test leaves Config.REVISION_TOOL_TURNS at 8 (the .env value is 48).

The canary in tests/test_zz_probe_config_leak.py reports Config.REVISION_TOOL_TURNS == 8
after the full suite, while .env says 48. That means some test mutated the Config class
without restoring it, and every test after it reads a value that no configuration
chose.

`monkeypatch.setattr` normally undoes itself. So the suspects are places that assign to
Config directly, or that reload the config module while a test's env values are set --
a reload builds a NEW class from the CURRENT environment, and if an env var is still
present at that moment the new class keeps it forever.

This script runs the suite one test file at a time and reports the value each file
leaves behind, so the leaking file is identified by bisection rather than guessed.

Usage:
    python tools/_find_config_leak.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = sorted((ROOT / "tests").glob("test_*.py"))

# Read the value in a FRESH process after importing every module the suite touches,
# so this measures the class default rather than a stale import.
PROBE = (
    "import sys; sys.path.insert(0, 'src');"
    "from config import Config;"
    "print(Config.REVISION_TOOL_TURNS)"
)


def baseline() -> str:
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=str(ROOT),
                         capture_output=True, text=True)
    return out.stdout.strip()


def main() -> int:
    print("=" * 78)
    print("  WHICH TEST LEAKS Config.REVISION_TOOL_TURNS?")
    print("=" * 78)
    base = baseline()
    print(f"\n  baseline (fresh process, .env only): REVISION_TOOL_TURNS = {base}")
    print(f"  .env says 48, so anything leaving 8 is a leak\n")

    # The canary test is not a suspect: it only reports.
    suspects = [p for p in TESTS if "probe" not in p.name and "zz" not in p.name]

    leaks: list[tuple[str, str]] = []
    for path in suspects:
        # Run this file together with the canary, which reads the value at the end.
        canary = ROOT / "tests" / "test_zz_probe_config_leak.py"
        out = subprocess.run(
            [sys.executable, "-m", "pytest", str(path), str(canary), "-q",
             "-p", "no:cacheprovider"],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        json_path = ROOT / ".tr_probe_observed.json"
        value = "?"
        if json_path.exists():
            import json

            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                value = str(data.get("REVISION_TOOL_TURNS", {}).get("Config_attr", "?"))
            except (OSError, json.JSONDecodeError):
                pass
        flag = "  <-- LEAKS" if value not in (base, "?") else ""
        print(f"  {path.name:<44} leaves {value}{flag}")
        if flag:
            leaks.append((path.name, value))

    print()
    print("=" * 78)
    if leaks:
        print("  LEAKING FILE(S):")
        for name, value in leaks:
            print(f"    {name} leaves REVISION_TOOL_TURNS={value} (expected {base})")
    else:
        print("  No single file leaks on its own. The leak needs a PAIR: a file that")
        print("  sets the env var, followed by one that reloads config while it is set.")
        print("  Run the full suite in file order and bisect on the ORDER instead.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
