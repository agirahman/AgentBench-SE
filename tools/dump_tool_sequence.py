"""Dump the raw tool sequence per act, to find ordering anomalies.

Act order can look wrong for reasons that are NOT bugs, and the common one is
invisible here: an act that made ZERO tool calls has no lines in
tool_calls.jsonl, so the file can start with the executor even though the planner
ran first. This prints enough context to tell a real anomaly from that.

Usage:
    python tools/dump_tool_sequence.py --exp EXP-20260930-332
    python tools/dump_tool_sequence.py --exp EXP-20260930-332 --instance django__django-11019
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exp", default=None)
    ap.add_argument("--instance", default=None)
    ap.add_argument("--strategy", default=None)
    args = ap.parse_args()

    exp = (ROOT / "results" / args.exp if args.exp
           else sorted((ROOT / "results").glob("EXP-*"), key=lambda p: p.stat().st_mtime)[-1])

    print("=" * 78)
    print(f"  RAW TOOL SEQUENCE -- {exp.name}")
    print("=" * 78)

    for inst_dir in sorted((exp / "artifacts").iterdir()):
        if not inst_dir.is_dir():
            continue
        if args.instance and args.instance not in inst_dir.name:
            continue
        for strategy in ("direct", "planning", "review"):
            if args.strategy and strategy != args.strategy:
                continue
            art = inst_dir / strategy
            tc = art / "tool_calls.jsonl"
            if not tc.exists():
                continue

            calls = [json.loads(l) for l in
                     tc.read_text(encoding="utf-8", errors="replace").splitlines()
                     if l.strip()]

            # Group into acts by consecutive agent.
            acts: list[dict] = []
            for c in calls:
                if not acts or acts[-1]["agent"] != c["agent"]:
                    acts.append({"agent": c["agent"], "tools": [], "details": []})
                acts[-1]["tools"].append(c["tool"])
                a = c.get("arguments") or {}
                desc = (a.get("path") or a.get("pattern") or a.get("command") or "")
                acts[-1]["details"].append(f"{c['tool']}({str(desc)[:40]})")

            print(f"\n  {inst_dir.name}  /  {strategy}")
            print(f"    acts in tool_calls.jsonl: {len(acts)}")
            for i, act in enumerate(acts, 1):
                edits = act["tools"].count("edit_file") + act["tools"].count("write_file")
                print(f"      act{i} {act['agent']:<9} n={len(act['tools']):<3} edits={edits}")
                print(f"           {' -> '.join(act['details'])}")

            # What does the blackboard say the act order was?
            m = art / "messages.jsonl"
            if m.exists():
                results = []
                for line in m.read_text(encoding="utf-8", errors="replace").splitlines():
                    if not line.strip():
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if d.get("kind") == "result":
                        results.append((d.get("sender"), len(d.get("tool_calls") or [])))
                print(f"    blackboard result order: "
                      f"{[f'{s}({n})' for s, n in results]}")


if __name__ == "__main__":
    main()
