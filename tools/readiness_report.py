"""One command that runs every readiness check and prints a single verdict table.

The individual tools each answer one question, and during this session several of them
disagreed (verify_revision_rounds.py flagged the CORRECT reserve as wrong because it
repeated the impossible `8 * rounds` arithmetic). Running them together makes a
disagreement visible instead of requiring the reader to notice it across separate runs.

Also strips the shell variables that override .env, because several checks were
measurably fooled by them during this session: "421 tests pass" was FALSE while
MAX_REVISION_TURNS=1 was exported.

Usage:
    python tools/readiness_report.py
    python tools/readiness_report.py --exp EXP-20260930-415
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Variables that override .env and have already produced false results once.
STRIP = ("TOTAL_TOOL_TURNS", "REVISION_TOOL_TURNS", "MAX_REVISION_TURNS",
         "BUDGET_MODE", "BUDGET_FLOOR_PER_ACT", "COST_LIMIT_USD")

CHECKS: list[tuple[str, list[str], str]] = [
    # FIRST, because it is the only check that covers the code the others merely
    # configure. A partner audit found this gate reporting "READY, all 7 checks pass"
    # while the unit suite was RED -- the 7 checks all passed because none of them
    # runs the tests. A gate that can be green with a broken suite is worse than no
    # gate: it converts "I did not check" into "I checked and it is fine".
    ("unit tests", ["-m", "pytest", "tests/", "-q"],
     "the suite passes (the other checks only verify configuration)"),
    ("preflight repos", ["tools/preflight_repos.py", "--quiet"],
     "every checkout pristine at its base_commit"),
    ("git sanitization", ["tools/sanitize_git_history.py", "--check"],
     "all checkouts are time-safe with zero future refs"),
    ("network block", ["tools/verify_network_block.py"],
     "sandbox sitecustomize and command filter actively block egress"),
    ("budget fairness", ["tools/check_budget_fairness.py"],
     "200/200/200 and every revision act can read AND edit"),
    ("revision rounds", ["tools/verify_revision_rounds.py"],
     "the configured reserve funds every configured round"),
    ("effective model", ["tools/check_effective_model.py"],
     "the sweep calls the PAID model, not the free one"),
    ("disk budget", ["tools/check_disk_budget.py"],
     "artifacts fit on disk for the whole sweep"),
    ("resume logic", ["tools/verify_resume_logic.py"],
     "completed runs are skipped, failures retried"),
    ("resume keys", ["tools/verify_resume_keys_match.py"],
     "real jsonl rows carry the fields the loader keys on"),
    ("sweep command", ["tools/run_final_sweep.py", "--dry-run"],
     "the sweep builds a command with every flag set explicitly"),
]


def run(cmd: list[str]) -> tuple[int, str]:
    env = os.environ.copy()
    for key in STRIP:
        env.pop(key, None)
    out = subprocess.run([sys.executable, *cmd], cwd=str(ROOT), env=env,
                         capture_output=True, text=True)
    return out.returncode, (out.stdout or "") + (out.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exp", default=None,
                    help="experiment id for the bill cross-check")
    ap.add_argument("--verbose", action="store_true",
                    help="print each check's full output, not just its verdict")
    args = ap.parse_args()

    print("=" * 78)
    print("  READINESS REPORT")
    print("=" * 78)
    stripped = [k for k in STRIP if k in os.environ]
    if stripped:
        print(f"\n  NOTE: ignoring shell overrides for {', '.join(stripped)}")
        print("  (they have produced false results before; .env is authoritative here)")

    checks = list(CHECKS)
    if args.exp:
        checks.append((
            "bill cross-check",
            ["tools/read_actual_bill.py", "--compare", f"results/{args.exp}"],
            "our cost model agrees with the real 9router bill",
        ))

    print()
    results: list[tuple[str, bool, str]] = []
    for name, cmd, purpose in checks:
        rc, out = run(cmd)
        ok = rc == 0
        results.append((name, ok, out))
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {name:<20} {purpose}")
        if args.verbose or not ok:
            for line in out.strip().splitlines()[-6:]:
                print(f"           {line}")
        print()

    failed = [n for n, ok, _ in results if not ok]
    print("=" * 78)
    if failed:
        print(f"  NOT READY: {len(failed)} check(s) failed -- {', '.join(failed)}")
    else:
        print(f"  READY: all {len(results)} checks pass.")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
