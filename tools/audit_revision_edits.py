"""Did the revision act actually EDIT anything?

The whole point of the carve-out reserve is that a rejected patch gets revised. Two
earlier experiments showed a revision act granted 1-4 turns spending all of them on
reads and making ZERO edits -- so the "revision" was decorative and the re-review
then approved an unchanged patch.

This answers the question directly from the artifact: for each revision act, list the
tools it called. An act with no edit_file/write_file cannot have changed the patch,
regardless of what the verdicts say.

Usage:
    python tools/audit_revision_edits.py --exp EXP-20260930-415
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EDIT_TOOLS = {"edit_file", "write_file"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--exp", required=True)
    ap.add_argument("--strategy", default="review")
    args = ap.parse_args()

    art = ROOT / "results" / args.exp / "artifacts"
    if not art.is_dir():
        print(f"no artifacts: {art}", file=sys.stderr)
        return 1

    found_any = False
    for inst_dir in sorted(p for p in art.iterdir() if p.is_dir()):
        traj = inst_dir / args.strategy / "trajectory.jsonl"
        if not traj.exists():
            continue

        by_act: dict[int, dict] = defaultdict(
            lambda: {"role": "", "turns": 0, "tools": []}
        )
        with traj.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                idx = row.get("act_index")
                if idx is None:
                    continue
                slot = by_act[idx]
                slot["role"] = row.get("act_role") or slot["role"]
                if row.get("type") == "assistant":
                    slot["turns"] += 1
                    for call in row.get("tool_calls") or []:
                        name = call.get("name") or call.get("function", {}).get("name")
                        if name:
                            slot["tools"].append(name)

        executor_acts = [i for i in sorted(by_act) if by_act[i]["role"] == "executor"]
        if len(executor_acts) < 2:
            continue

        found_any = True
        print(f"  {inst_dir.name}")
        for n, idx in enumerate(executor_acts):
            slot = by_act[idx]
            edits = [t for t in slot["tools"] if t in EDIT_TOOLS]
            label = "base    " if n == 0 else f"revision{n}"
            verdict = "EDITED" if edits else "NO EDIT"
            print(f"    {label}: {slot['turns']:>2} turns, "
                  f"{len(slot['tools'])} calls, edits={len(edits)}  -> {verdict}")
        rev_edits = sum(
            len([t for t in by_act[i]["tools"] if t in EDIT_TOOLS])
            for i in executor_acts[1:]
        )
        print(f"    total revision edits: {rev_edits}")
        print()

    if not found_any:
        print("  no instance ran a revision round in this experiment")
        print("  (either every patch was approved first, or no revision was allowed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
