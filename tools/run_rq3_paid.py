"""RQ3: the paid run. Same 3 issues, same config, but a model that bills.

Why this exists
---------------
Every previous run used a free model and priced its tokens with a borrowed rate
card (PRICING_MODEL_OVERRIDE). That produces an ESTIMATE, and RQ3 is a question
about money: "what does an agentic strategy cost to resolve an issue?" An estimate
is a weaker answer than a bill.

This run uses cbai/deepseek-v4.1-flash through 9router, which records every
request's tokens and cost in its own database. So the cost column is not modelled:
it can be read back from the bill with tools/read_actual_bill.py and compared
against our own accounting.

Config is deliberately IDENTICAL to the budget-curve level 40 (per_task, floor 10,
40 turns, 3 issues) so the paid run is comparable to the free one. The only
differences are the model and the fact that cost is now real.

Usage
-----
    python tools/run_rq3_paid.py --dry-run
    python tools/run_rq3_paid.py --smoke        # 1 issue x 1 strategy, ~5 min
    python tools/run_rq3_paid.py                # full: 3 issues x 3 strategies

The start timestamp is written to logs/rq3_started.json so the bill window is
recoverable even if the shell that launched the run is gone.
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
ISSUES = [
    "django__django-10914",  # control: resolved in all three strategies
    "django__django-11001",  # review failed at level 40, resolved at 100
    "django__django-11019",  # never resolved in any strategy at any level
]

# Matches the curve's level 40 exactly, so the two runs differ only in the model.
TOTAL_TURNS = 40
FLOOR = 10

# Dollar backstop for ONE task. Expected cost is ~$0.15/run (measured: this route
# averages ~124k prompt tokens per request, ~90% of it cached, and a run makes
# ~40 requests), so $1.00 does not bind in normal operation -- it exists so a
# pathological loop cannot spend without bound on a paid route.
COST_LIMIT_USD = 1.00


def build_cmd(issues: list[str], strategies: list[str], dry_run: bool) -> list[str]:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "run_with_env.py"),
        # The paid model has a MEASURED rate card in PricingTable, so no
        # PRICING_MODEL_OVERRIDE: the cost columns are real, not an estimate.
        "--set", f"OPENCODE_MODEL={MODEL}",
        "--set", f"TOTAL_TOOL_TURNS={TOTAL_TURNS}",
        "--set", "BUDGET_MODE=per_task",
        "--set", f"BUDGET_FLOOR_PER_ACT={FLOOR}",
        "--set", "REVISION_TOOL_TURNS=0",
        "--set", f"COST_LIMIT_USD={COST_LIMIT_USD}",
        "--provider", "opencode",
        "--strategies", *strategies,
        "--instance-ids", *issues,
        "--rate-limit", "2.0",
    ]
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(description="RQ3 paid run (sequential).")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--smoke", action="store_true",
                    help="one issue, one strategy -- verify the paid path end to end")
    ap.add_argument("--issues", nargs="*", default=ISSUES)
    ap.add_argument("--strategies", nargs="*", default=["direct", "planning", "review"])
    args = ap.parse_args()

    issues = args.issues
    strategies = args.strategies
    if args.smoke:
        issues = [ISSUES[0]]
        strategies = ["direct"]

    runs = len(issues) * len(strategies)
    print("=" * 78)
    print("  RQ3 -- PAID RUN")
    print("=" * 78)
    print(f"  model      : {MODEL}")
    print(f"  issues     : {len(issues)} ({', '.join(i.split('-')[-1] for i in issues)})")
    print(f"  strategies : {', '.join(strategies)}")
    print(f"  runs       : {runs}")
    print(f"  budget     : per_task, total {TOTAL_TURNS}, floor {FLOOR}")
    print(f"  cost cap   : ${COST_LIMIT_USD:.2f} per task (backstop)")
    print(f"  pricing    : MEASURED card ($0.14/$0.0028/$0.28 per 1M) -- real money")
    print()

    cmd = build_cmd(issues, strategies, args.dry_run)
    if args.dry_run:
        print(" ".join(cmd[1:]))
        return 0

    started = datetime.now(timezone.utc)
    state = {
        "model": MODEL,
        "issues": issues,
        "strategies": strategies,
        "started_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "started_iso": started.isoformat(),
        "total_turns": TOTAL_TURNS,
        "floor": FLOOR,
        "cost_limit_usd": COST_LIMIT_USD,
    }
    state_path = ROOT / "logs" / "rq3_started.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print(f"  bill window starts: {state['started_utc']}")
    print(f"  (read it later: python tools/read_actual_bill.py --since "
          f"{state['started_utc']} --model {MODEL})")
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
    print(f"  RQ3 RUN DONE  rc={rc}  in {dt / 60:.1f} min")
    print(f"  bill window: {state['started_utc']} .. {state['finished_utc']}")
    print("=" * 78)
    print()
    print("  Next: read the real bill and compare it to our accounting:")
    print(f"    python tools/read_actual_bill.py --since {state['started_utc']} "
          f"--until {state['finished_utc']} --model {MODEL} --compare results/<EXP-id>")
    return rc


if __name__ == "__main__":
    sys.exit(main())
