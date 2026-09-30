"""The 50-issue sweep: 3 strategies x 50 instances, one paid model.

This is the final run the thesis rests on, so the config is fixed here rather than
left to .env -- a stray shell variable silently changing the budget is the exact
class of problem that made earlier results non-comparable (see MEMORY, config
drift). Every knob is passed explicitly through tools/run_with_env.py.

What is deliberately different from the earlier 3-issue runs
-----------------------------------------------------------

1. REVISION_TOOL_TURNS=8 (was 0).

   With 0, review's revision act was granted the floor of 1 turn, so it could make
   one tool call and never edit. Measured across EXP-20260929-003 and
   EXP-20260929-022: "Tool loop hit max_tool_turns=1 for role=executor" on the
   revision act, 0 edits. That means every "review" number produced so far measures
   ONE executor act plus a decorative round, not review+revision.

   The reserve is CARVED OUT of TOTAL_TOOL_TURNS, so the totals stay equal:
   direct 40, planning 40, review 32+8=40. An earlier version added it on top and
   gave review 48 turns, which would confound any claim that review is better.

2. COST_LIMIT_USD=3.00 (the reference value).

   mini-SWE-agent and SWE-agent both cap a task at $3. The measured cost of a run
   on this route is ~$0.03, so it does not bind -- it exists so a pathological loop
   cannot spend without bound on a paid route. The 3-issue RQ3 run used $1.00,
   which was also non-binding; $3 matches the reference.

3. ACT_TIMEOUT_SECONDS=1800.

   A turn budget does not bound duration: with the rate-limit backoff schedule one
   request can sleep 180s before failing, and a 40-turn act is 41 requests. A
   single review run was measured at 5,992 s (100 minutes). At 150 runs that is
   hours of pure waiting, and the rate-limit breaker cannot catch it because the
   backoff happens inside the tool loop.

Cost and time
-------------

Measured on the 3-issue paid run: $0.298 for 9 runs, 14.9 minutes. Extrapolated:
150 runs ~= $5 and ~4-6 hours, depending on how often review revises. The review
arm will cost MORE per run than before because revisions now actually happen --
that is the point, and it is a real finding rather than a defect.

Usage
-----
    python tools/run_final_sweep.py --dry-run     # show the command, run nothing
    python tools/run_final_sweep.py --smoke       # 1 issue x 1 strategy
    python tools/run_final_sweep.py --limit 5     # first 5 issues (15 runs)
    python tools/run_final_sweep.py               # the full 150 runs

The start timestamp is written to logs/sweep_started.json so the bill window is
recoverable even if the shell that launched the run is gone, and so
tools/read_actual_bill.py can be pointed at it afterwards.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MODEL = "cbai/deepseek-v4.1-flash"

#: The whole task's turn allowance. Identical for all three strategies.
TOTAL_TURNS = 40
#: Turns review sets aside for revising, carved out of TOTAL_TURNS.
REVISION_TURNS = 8
#: Guaranteed turns for each act still to come, in per_task mode.
FLOOR = 10
#: Dollar backstop per task -- the reference implementations' value ($3).
COST_LIMIT_USD = 3.00
#: Wall-clock bound per act, so retry backoff cannot hang a run for hours.
ACT_TIMEOUT_SECONDS = 1800


def select_issues(limit: int | None) -> list[str]:
    """Pick the instances that have a local checkout, deterministically.

    Ordered by instance id so the selection is reproducible and a `--limit` run
    covers the same instances every time; an arbitrary order would make two
    partial runs incomparable.

    Resolution checks the two documented layouts DIRECTLY rather than calling
    agents.tools.resolve_repo_root. That function's last resort is an os.walk over
    the owner directory -- 1.35 GB across 51 checkouts -- and calling it once per
    instance (300 times, mostly for instances that are not cached) does not finish
    in a reasonable time. Only instances with a checkout are runnable anyway, and
    without one the runner falls back to the shared sandbox base where the agent
    reads ANOTHER instance's files.
    """
    from datasets import load_dataset

    sys.path.insert(0, str(ROOT / "src"))
    from config import Config

    base = Path(Config.TOOLCALL_REPO_DIR)
    if not base.is_absolute():
        base = ROOT / base

    ds = load_dataset("SWE-bench/SWE-bench_Lite", split="test")

    usable: list[str] = []
    for row in sorted(ds, key=lambda r: r["instance_id"]):
        owner, _, name = row["repo"].partition("/")
        commit = row["base_commit"]
        if (base / owner / name / commit).is_dir() or (base / owner / commit).is_dir():
            usable.append(row["instance_id"])

    if limit is not None:
        return usable[:limit]
    return usable


def build_cmd(issues: list[str], strategies: list[str], dry_run: bool) -> list[str]:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "run_with_env.py"),
        "--set", f"OPENCODE_MODEL={MODEL}",
        "--set", f"TOTAL_TOOL_TURNS={TOTAL_TURNS}",
        "--set", "BUDGET_MODE=per_task",
        "--set", f"BUDGET_FLOOR_PER_ACT={FLOOR}",
        # The reserve that makes the review arm measure review+revision. Carved
        # out of TOTAL_TOOL_TURNS, so every strategy still gets 40 turns.
        "--set", f"REVISION_TOOL_TURNS={REVISION_TURNS}",
        "--set", f"COST_LIMIT_USD={COST_LIMIT_USD}",
        "--set", f"ACT_TIMEOUT_SECONDS={ACT_TIMEOUT_SECONDS}",
        "--provider", "opencode",
        "--strategies", *strategies,
        "--instance-ids", *issues,
        "--rate-limit", "2.0",
    ]
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(description="Final 50-issue sweep (sequential).")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the command and exit")
    ap.add_argument("--smoke", action="store_true",
                    help="1 issue x 1 strategy -- verify the path end to end first")
    ap.add_argument("--limit", type=int, default=None,
                    help="use only the first N usable issues (for a pilot run)")
    ap.add_argument("--strategies", nargs="*", default=["direct", "planning", "review"])
    ap.add_argument("--issues", nargs="*", default=None,
                    help="explicit instance ids (overrides --limit)")
    args = ap.parse_args()

    if args.issues:
        issues = args.issues
    elif args.smoke:
        issues = select_issues(1)
    else:
        issues = select_issues(args.limit)

    strategies = ["direct"] if args.smoke else args.strategies
    runs = len(issues) * len(strategies)

    print("=" * 78)
    print("  FINAL SWEEP")
    print("=" * 78)
    print(f"  model      : {MODEL}")
    print(f"  issues     : {len(issues)}")
    if len(issues) <= 8:
        for i in issues:
            print(f"               {i}")
    else:
        print(f"               {issues[0]} ... {issues[-1]}")
    print(f"  strategies : {', '.join(strategies)}")
    print(f"  runs       : {runs}")
    print(f"  budget     : per_task, total {TOTAL_TURNS} turns per strategy")
    print(f"               direct {TOTAL_TURNS} | planning {TOTAL_TURNS} | "
          f"review {TOTAL_TURNS - REVISION_TURNS}+{REVISION_TURNS} = {TOTAL_TURNS}")
    print(f"  cost cap   : ${COST_LIMIT_USD:.2f} per task (backstop, reference value)")
    print(f"  act timeout: {ACT_TIMEOUT_SECONDS}s per act (bounds retry backoff)")
    print()
    print(f"  Fairness   : every strategy's task budget is {TOTAL_TURNS} turns, so a")
    print(f"               review win cannot be explained by a larger budget.")
    print(f"               Review sets {REVISION_TURNS} of its own turns aside to revise.")
    print()
    print("  Check first: python tools/preflight_repos.py")
    print()

    cmd = build_cmd(issues, strategies, args.dry_run)
    if args.dry_run:
        print("Command:")
        print("  " + " ".join(cmd[1:]))
        return 0

    started = datetime.now(timezone.utc)
    state = {
        "model": MODEL,
        "issues": issues,
        "strategies": strategies,
        "runs_planned": runs,
        "started_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "started_iso": started.isoformat(),
        "total_tool_turns": TOTAL_TURNS,
        "revision_tool_turns": REVISION_TURNS,
        "budget_floor_per_act": FLOOR,
        "cost_limit_usd": COST_LIMIT_USD,
        "act_timeout_seconds": ACT_TIMEOUT_SECONDS,
    }
    state_path = ROOT / "logs" / "sweep_started.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print(f"  bill window starts: {state['started_utc']}")
    print()
    print("=" * 78, flush=True)

    t0 = time.time()
    rc = subprocess.call(cmd, cwd=str(ROOT))
    dt = time.time() - t0

    state["finished_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["elapsed_minutes"] = round(dt / 60, 1)
    state["rc"] = rc
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print()
    print("=" * 78)
    print(f"  SWEEP DONE  rc={rc}  in {dt / 60:.1f} min")
    print(f"  bill window: {state['started_utc']} .. {state['finished_utc']}")
    print("=" * 78)
    print()
    print("  Verify before analysing:")
    print("    python tools/check_sweep_state.py --exp <EXP-id>     # every run present?")
    print("    python tools/verify_eval_consistency.py             # after evaluating")
    print("    python tools/read_actual_bill.py \\")
    print(f"      --since {state['started_utc']} --until {state['finished_utc']} \\")
    print(f"      --model {MODEL} --compare results/<EXP-id>")
    return rc


if __name__ == "__main__":
    sys.exit(main())
