"""Prove the readiness gate FAILS when the suite is red.

The gate's whole value is that it cannot say READY while something is broken. A first
version did exactly that: it ran 7 configuration checks and no tests, so it printed
"READY: all 7 checks pass" while the unit suite was red. A partner audit caught it.

This breaks one test on purpose and confirms the gate reports NOT READY. Restores the
file from an in-memory copy, always.

Usage:
    python tools/_prove_gate_fails.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "tests" / "test_budget_modes.py"

original = TARGET.read_text(encoding="utf-8")
marker = "def test_no_reserve_keeps_the_legacy_starved_behaviour():"
if marker not in original:
    print(f"anchor not found in {TARGET.name}; aborting rather than guessing")
    sys.exit(2)

# Insert a deliberately failing test at the top of the file.
broken = original.replace(
    marker,
    'def test_deliberately_broken_for_the_gate_check():\n'
    '    assert False, "injected failure: the readiness gate must not say READY"\n\n\n'
    + marker,
    1,
)

try:
    TARGET.write_text(broken, encoding="utf-8")
    out = subprocess.run(
        [sys.executable, "tools/readiness_report.py"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    text = (out.stdout or "") + (out.stderr or "")
    print("--- gate output with a deliberately broken test ---")
    for line in text.splitlines():
        if any(k in line for k in ("[PASS]", "[FAIL]", "READY", "failed", "unit tests")):
            print(f"  {line}")
    print()

    said_ready = "READY: all" in text
    flagged_tests = "[FAIL] unit tests" in text
    print(f"  gate said READY          : {said_ready}   (must be False)")
    print(f"  gate flagged unit tests  : {flagged_tests}   (must be True)")
    print(f"  exit code                : {out.returncode}   (must be non-zero)")
    print()

    ok = (not said_ready) and flagged_tests and out.returncode != 0
    if ok:
        print("OK -- the gate FAILS with a red suite. It is load-bearing.")
    else:
        print("PROBLEM -- the gate can say READY while the suite is red.")
    sys.exit(0 if ok else 1)
finally:
    TARGET.write_text(original, encoding="utf-8")
    print("\nfile restored")
