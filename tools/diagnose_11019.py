"""Did 11019 fail because of the budget, or because it is beyond the model?

This question was left open across three earlier experiments (EXP-20260929-003,
EXP-20260929-022) and matters for how the thesis describes it:

  * BUDGET-LIMITED  -> the agent ran out of turns, so the number measures our cap;
  * CAPABILITY      -> the agent finished within its budget and its fix was wrong.

At 200 turns with the planner now grounded, the distinction is finally measurable:
if 11019's runs were truncated, the budget is still the story. If they completed
normally and still failed, it is a capability limit -- and the gold patch passing
1/1 (established earlier) confirms the instance IS gradable, so the failure is real
rather than a harness artifact.

Usage:
    python tools/diagnose_11019.py --exp EXP-20260930-415
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = "django__django-11019"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exp", default=None)
    args = ap.parse_args()

    exp = (ROOT / "results" / args.exp if args.exp
           else sorted((ROOT / "results").glob("EXP-*"), key=lambda p: p.stat().st_mtime)[-1])

    print("=" * 78)
    print(f"  {TARGET} -- budget-limited or capability? ({exp.name})")
    print("=" * 78)

    log = exp / "logs" / "experiment.log"
    log_text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""

    for strategy in ("direct", "planning", "review"):
        art = exp / "artifacts" / TARGET / strategy
        if not art.is_dir():
            continue

        print(f"\n  {'=' * 74}")
        print(f"  {strategy}")
        print(f"  {'=' * 74}")

        # 1. Was any act truncated?
        truncations = []
        current = None
        for line in log_text.splitlines():
            m = re.search(r"Running (\w+) on (\S+)", line)
            if m:
                current = f"{m.group(1)}/{m.group(2)}"
            t = re.search(r"max_tool_turns=(\d+) for role=(\w+)", line)
            if t and current and TARGET in current and strategy in current:
                truncations.append((t.group(2), int(t.group(1))))
            if ("context limit" in line or "stopped on" in line) and current and TARGET in current:
                truncations.append((line.strip()[:70], -1))

        print(f"\n    truncated acts: {len(truncations)}")
        for role, turns in truncations:
            print(f"      {role} granted {turns} turns, used them all"
                  if turns > 0 else f"      {role}")

        # 2. How many turns did each act actually use?
        tc = art / "tool_calls.jsonl"
        if tc.exists():
            calls = [json.loads(l) for l in
                     tc.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
            acts: list[tuple[str, int, int]] = []
            for c in calls:
                if not acts or acts[-1][0] != c["agent"]:
                    acts.append((c["agent"], 0, 0))
                agent, n, edits = acts[-1]
                n += 1
                if c["tool"] in ("edit_file", "write_file"):
                    edits += 1
                acts[-1] = (agent, n, edits)
            print(f"\n    turns per act:")
            for agent, n, edits in acts:
                print(f"      {agent:<9} calls={n:<3} edits={edits}")

        # 3. What did the run produce?
        summary = art / "summary.json"
        if summary.exists():
            d = json.loads(summary.read_text(encoding="utf-8"))
            print(f"\n    patch_status : {d.get('patch_status')}")
            print(f"    elapsed      : {d.get('elapsed_seconds', 0):.0f}s")
            print(f"    tokens       : {d.get('total_tokens'):,}"
                  if d.get("total_tokens") else "")

        patch = art / "patch.txt"
        if patch.exists():
            text = patch.read_text(encoding="utf-8", errors="replace")
            files = re.findall(r"^diff --git a/(\S+)", text, re.M)
            print(f"    patch touches: {files}")
            print(f"    patch size   : {len(text)} chars")

    print()
    print("=" * 78)
    print("  HOW TO READ THIS")
    print("=" * 78)
    print("""
  If `truncated acts: 0` and the acts used far fewer turns than they were granted,
  the agent STOPPED ON ITS OWN and its fix was still wrong. That is a capability
  limit at this budget, not a budget limit -- and the gold patch passing 1/1 on this
  instance (verified earlier) rules out a harness artifact.

  If an act hit its cap, the run measures the cap and the instance must be reported
  as budget-confounded instead.
""")


if __name__ == "__main__":
    main()
