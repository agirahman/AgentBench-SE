"""Why does the LOG look like the executor runs first, with no planner?

The log prints one ``[toolcall] role=X`` line per executed call, so an act that made
ZERO tool calls prints NOTHING. If the planner answers from the issue text without
reading any code, the log jumps straight from the run header to the executor -- and
it looks like the planner was skipped.

This compares, per run:
  * the log's first toolcall role (what a reader sees)
  * the blackboard's result order (what actually happened)
  * how many tool calls each act made

Usage:
    python tools/diagnose_act_order_in_log.py --exp EXP-20260930-332
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exp", default=None)
    args = ap.parse_args()

    exp = (ROOT / "results" / args.exp if args.exp
           else sorted((ROOT / "results").glob("EXP-*"), key=lambda p: p.stat().st_mtime)[-1])

    log = exp / "logs" / "experiment.log"
    text = log.read_text(encoding="utf-8", errors="replace")

    print("=" * 78)
    print(f"  WHAT THE LOG SHOWS vs WHAT HAPPENED -- {exp.name}")
    print("=" * 78)

    # Walk the log, tracking the current run and the roles that printed a toolcall.
    current = None
    log_roles: dict[str, list[str]] = {}
    for line in text.splitlines():
        m = re.search(r"Running (\w+) on (\S+)", line)
        if m:
            current = f"{m.group(1)}/{m.group(2)}"
            log_roles.setdefault(current, [])
            continue
        t = re.search(r"\[toolcall\] role=(\w+)", line)
        if t and current:
            role = t.group(1)
            if role not in log_roles[current]:
                log_roles[current].append(role)

    print(f"\n  {'run':<34} {'log shows (in order)':<28} {'blackboard result order'}")
    print(f"  {'-' * 92}")

    skipped_planner = 0
    for run, roles in log_roles.items():
        strategy, inst = run.split("/", 1)
        art = exp / "artifacts" / inst / strategy
        m = art / "messages.jsonl"

        bb_order = []
        if m.exists():
            for line in m.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("kind") == "result":
                    bb_order.append(f"{d.get('sender')}({len(d.get('tool_calls') or [])})")

        log_str = " -> ".join(roles) if roles else "(no toolcalls)"
        bb_str = " -> ".join(bb_order)

        # The point of this script: a planner in the blackboard but not in the log.
        flag = ""
        if "planner" in bb_str and "planner" not in log_str:
            flag = "   <-- planner ran, made 0 calls"
            skipped_planner += 1

        print(f"  {run:<34} {log_str:<28} {bb_str}{flag}")

    print()
    print("=" * 78)
    print(f"  {skipped_planner} run(s) where the planner RAN but printed no toolcall line")
    print("=" * 78)
    if skipped_planner:
        print("""
  This is not an act-order bug. The planner runs FIRST and produces a plan, but if
  it calls no tools, the log has nothing to print for it -- the log only records
  tool calls. Reading the log alone therefore makes the executor look like the
  first act.

  The real question is WHY the planner calls no tools: it has read_file, grep and
  list_files, and it is supposed to base its plan on code it has actually read.
""")
    else:
        print("  Every act that ran also printed at least one toolcall line.")


if __name__ == "__main__":
    main()
