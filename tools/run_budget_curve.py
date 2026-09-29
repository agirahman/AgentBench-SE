"""Run the budget curve: 3 issues x 3 budget levels x 3 strategies.

Why a curve and not a single number
-----------------------------------
METR's finding, and the reason this script exists: there is no defensible way to
pick a turn budget a priori. Any constant is arbitrary, so accuracy must be
reported as a FUNCTION of the budget, with the curve as the result. "Why 40?"
currently has no methodological answer -- the trail in HANDOFF_20260928.md says
40 was chosen to cut wall-clock, not because 40 measured better than 60. This
curve replaces that with measurement.

Levels
------
    40   the current pool, and the only point with an existing comparator
         (EXP-20260928-003). Included so the new per-task rule can be compared
         against the old per-act rule at the SAME number.
    100  the proposed reference-comparable pool (mini-SWE-agent uses 250 steps
         per task, OpenHands 500, SWE-bench Pro 200; none splits per agent).
    200  the upper bound. If accuracy is flat here too, the curve is flat and
         that is itself the finding -- it means the references' larger budgets
         buy nothing on these instances.

Sequential, by explicit decision: running levels in parallel would put three
concurrent load streams on the same 9router and the same free model. Each level
is one process, one run at a time.

Issue selection is adversarial on purpose: 11019 failed in ALL three strategies
under the old budget (and all three hit the cap), 11001's review failure was a
starved revision the reviewer had correctly diagnosed, and 10914 is a control
that succeeded. Picking three already-passing issues would produce a flat curve
that measures nothing.

Usage
-----
    python tools/run_budget_curve.py --levels 40 100 200 --dry-run
    python tools/run_budget_curve.py --levels 40 100 200

Each level is a separate experiment (its own EXP id and directory). Token counts
are priced with the paid DeepSeek card via PRICING_MODEL_OVERRIDE, because the
testing model is free: the run is cheap, but RQ3 needs dollar figures that are
not all zero.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ISSUES = [
    "django__django-11019",  # failed in all 3 strategies; all 3 hit the cap
    "django__django-11001",  # review failed: revision starved at 1 turn
    "django__django-10914",  # control: succeeded in all 3
]

# Floor per remaining act, in per_task mode. Chosen as 10, not 5: with 5 the
# reviewer could be squeezed to 5 turns, and review failing for lack of REVIEW
# turns would turn the "reviewer has no discriminative power" finding into a
# budget artefact -- the exact class of error this curve exists to remove.
FLOOR = 10


def build_cmd(level: int, dry_run: bool) -> list[str]:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "run_with_env.py"),
        "--set", f"TOTAL_TOOL_TURNS={level}",
        # per_task: one pool for the whole task, floor reserved for later acts.
        "--set", "BUDGET_MODE=per_task",
        "--set", f"BUDGET_FLOOR_PER_ACT={FLOOR}",
        # No separate revision reserve in per_task mode; the pool funds it.
        "--set", "REVISION_TOOL_TURNS=0",
        # Dollar guard. Measured worst case is ~$0.07/run, so this never binds
        # in practice -- it is a backstop, not a limit we expect to reach.
        "--set", "COST_LIMIT_USD=3.0",
        # Price the free testing model with the paid card so cost columns and
        # RQ3 are not all zero. This is an ESTIMATE, recorded as such.
        "--set", "PRICING_MODEL_OVERRIDE=deepseek-v4-flash",
        "--provider", "opencode",
        "--strategies", "direct", "planning", "review",
        "--instance-ids", *ISSUES,
        "--rate-limit", "2.0",
    ]
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(description="Budget curve sweep (sequential).")
    ap.add_argument("--levels", nargs="+", type=int, default=[40, 100, 200])
    ap.add_argument("--dry-run", action="store_true", help="print commands only")
    ap.add_argument("--skip", nargs="*", type=int, default=[],
                    help="levels to skip (e.g. --skip 200 to resume a sweep)")
    args = ap.parse_args()

    levels = [lv for lv in args.levels if lv not in args.skip]
    total_runs = len(levels) * len(ISSUES) * 3

    print("=" * 78)
    print("  BUDGET CURVE")
    print("=" * 78)
    print(f"  levels     : {levels}")
    print(f"  issues     : {len(ISSUES)} ({', '.join(i.split('-')[-1] for i in ISSUES)})")
    print(f"  strategies : direct, planning, review")
    print(f"  total runs : {total_runs}")
    print(f"  mode       : per_task, floor {FLOOR}, cost cap $3.00")
    print(f"  pricing    : free model, tokens priced with the DeepSeek card")
    print()

    if args.dry_run:
        for lv in levels:
            print(" ".join(build_cmd(lv, dry_run=False)[1:]))
            print()
        return 0

    started = time.time()
    failures: list[int] = []
    state_path = ROOT / "logs" / "budget_curve_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)

    for idx, level in enumerate(levels, 1):
        cmd = build_cmd(level, dry_run=False)
        print("=" * 78)
        print(f"  LEVEL {level}  ({idx}/{len(levels)})   {len(ISSUES) * 3} runs")
        print("=" * 78, flush=True)

        # State file, written BEFORE the level starts and cleared after it ends.
        # Two jobs: the watcher needs to know which level is in flight (during a
        # level the experiment directory has no config yet, because main.py
        # writes experiment.yaml only at the end), and a sweep killed mid-flight
        # leaves a record of what was running. The log file is not usable for
        # this -- PowerShell's Tee-Object writes it as UTF-16, so a naive reader
        # sees a null byte after every character and matches nothing.
        state_path.write_text(
            json.dumps({
                "level": level,
                "index": idx,
                "of": len(levels),
                "issues": ISSUES,
                "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "finished": False,
            }, indent=2),
            encoding="utf-8",
        )

        t0 = time.time()
        # No capture: the level's own log is the progress record, and a sweep
        # that swallows output cannot be monitored while it runs.
        rc = subprocess.call(cmd, cwd=str(ROOT))
        dt = time.time() - t0
        print(f"\n  level {level} finished rc={rc} in {dt / 60:.1f} min\n", flush=True)
        if rc != 0:
            failures.append(level)

    state_path.write_text(
        json.dumps({
            "finished": True,
            "levels": levels,
            "failures": failures,
            "elapsed_minutes": (time.time() - started) / 60,
        }, indent=2),
        encoding="utf-8",
    )

    print("=" * 78)
    print(f"  SWEEP DONE in {(time.time() - started) / 60:.1f} min")
    if failures:
        print(f"  levels with non-zero exit: {failures}")
    print("=" * 78)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
