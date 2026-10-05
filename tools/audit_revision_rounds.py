"""How many revision rounds actually ran, and how were the turns granted?

Partner audit (docs/AUDIT_SCALE_PARTNER.md) raised a blocker: the sweep configures
REVISION_TOOL_TURNS=32, documented as "4 rounds x 8", while .env sets
MAX_REVISION_TURNS=1 -- so review runs ONE round and 16 reserved turns are never
granted. That claim is checkable against the artifacts, and the answer decides
whether the sweep would have measured the strategy we think it measures.

This reads the per-act record out of trajectory.jsonl (act_index / act_role, written
by the tool loop) and reports the turn count each act actually used, so the grant
per round is measured rather than inferred.

Usage:
    python tools/audit_revision_rounds.py --exp EXP-20260930-415
    python tools/audit_revision_rounds.py --exp EXP-20260930-415 --instance django__django-11019
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def act_turns(traj_path: Path) -> list[tuple[int, str, int, int]]:
    """(act_index, act_role, assistant_turns, tool_calls) per act, in order."""
    by_act: dict[int, dict] = defaultdict(lambda: {"role": "", "turns": 0, "calls": 0})
    with traj_path.open(encoding="utf-8") as fh:
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
            slot["calls"] += len(row.get("tool_calls") or [])

    return [(i, by_act[i]["role"], by_act[i]["turns"], by_act[i]["calls"])
            for i in sorted(by_act)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--exp", required=True, help="experiment directory name")
    ap.add_argument("--instance", default=None, help="limit to one instance")
    ap.add_argument("--strategy", default="review", help="strategy to inspect")
    args = ap.parse_args()

    art = ROOT / "results" / args.exp / "artifacts"
    if not art.is_dir():
        print(f"no artifacts directory: {art}", file=sys.stderr)
        return 1

    instances = sorted(p.name for p in art.iterdir() if p.is_dir())
    if args.instance:
        instances = [i for i in instances if i == args.instance]
    if not instances:
        print("no matching instances", file=sys.stderr)
        return 1

    total_rounds = 0
    for inst in instances:
        traj = art / inst / args.strategy / "trajectory.jsonl"
        if not traj.exists():
            print(f"  {inst}: no trajectory.jsonl (run did not reach the tool loop)")
            continue
        acts = act_turns(traj)
        print(f"  {inst}")
        for idx, role, turns, calls in acts:
            print(f"    act {idx}: {role:<9} turns={turns:<3} tool_calls={calls}")
        # A revision round shows up as a SECOND executor act.
        executor_acts = [a for a in acts if a[1] == "executor"]
        rounds = max(0, len(executor_acts) - 1)
        total_rounds += rounds
        if rounds:
            rev_turns = [a[2] for a in executor_acts[1:]]
            print(f"    -> {rounds} revision round(s), revision turns used: {rev_turns}")

    print()
    print(f"  instances: {len(instances)}   revision rounds seen: {total_rounds}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
