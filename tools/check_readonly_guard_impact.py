"""How often does a read-only role write into the repo, and does the guard hurt it?

The guard refuses a repo write from planner/reviewer. Two things need measuring
before a 150-run sweep, because both are real risks:

1. **Is it frequent?** If the reviewer constantly tries to write into the repo, the
   guard fires often and its refusals consume turns -- which would distort the
   review arm's budget and its results.

2. **Does the reviewer recover?** A refusal that stops the verification effort is
   worse than the hole it closed. What matters is whether the reviewer then uses a
   read-only route (write to /tmp, or just read the code) and reaches a verdict.

This reports both, across every experiment on disk, and lists what the reviewer did
immediately AFTER a refusal.

Usage:
    python tools/check_readonly_guard_impact.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents import tools as T  # noqa: E402


def main() -> None:
    exps = sorted((ROOT / "results").glob("EXP-*"), key=lambda p: p.stat().st_mtime)

    print("=" * 78)
    print("  READ-ONLY GUARD IMPACT -- across every experiment on disk")
    print("=" * 78)

    total_calls = 0
    total_refused = 0
    per_exp: list[tuple[str, int, int]] = []
    refusals: list[tuple[str, str, str, str]] = []

    for exp in exps:
        calls = refused = 0
        for tc in sorted(exp.glob("artifacts/*/*/tool_calls.jsonl")):
            for line in tc.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    c = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if c.get("agent") not in ("planner", "reviewer"):
                    continue
                calls += 1
                res = c.get("result_preview") or ""
                if "read-only" in res and "may not modify" in res:
                    refused += 1
                    refusals.append((
                        exp.name,
                        tc.parent.parent.name,
                        tc.parent.name,
                        (c.get("arguments") or {}).get("command", ""),
                    ))
        if calls:
            per_exp.append((exp.name, calls, refused))
            total_calls += calls
            total_refused += refused

    print(f"\n  {'experiment':<26} {'read-only calls':>16} {'refused':>9}")
    print(f"  {'-' * 54}")
    for name, calls, refused in per_exp:
        flag = "" if not refused else "   <-- guard fired"
        print(f"  {name:<26} {calls:>16} {refused:>9}{flag}")

    print()
    print(f"  TOTAL: {total_refused} refused of {total_calls} read-only calls "
          f"({total_refused / total_calls * 100:.1f}%)" if total_calls else "  no data")

    if refusals:
        print()
        print("=" * 78)
        print("  WHAT WAS REFUSED, AND WHAT THE ROLE DID NEXT")
        print("=" * 78)
        for exp_name, inst, strategy, command in refusals:
            print(f"\n  {exp_name}  {inst} / {strategy}")
            print(f"    command: {command[:150]}")
            print(f"    classifier: {T._command_looks_like_a_write(command)!r}")

            # What did the same role do on its next calls?
            art = ROOT / "results" / exp_name / "artifacts" / inst / strategy
            tc = art / "tool_calls.jsonl"
            if not tc.exists():
                continue
            calls = [json.loads(l) for l in
                     tc.read_text(encoding="utf-8", errors="replace").splitlines()
                     if l.strip()]
            idx = next((i for i, c in enumerate(calls)
                        if (c.get("arguments") or {}).get("command", "") == command), None)
            if idx is None:
                continue
            print("    next calls:")
            for c in calls[idx + 1:idx + 4]:
                arg = (c.get("arguments") or {}).get("command", "")
                detail = arg[:70] if arg else (
                    (c.get("arguments") or {}).get("path", "") or "")
                print(f"      {c['agent']:<9} {c['tool']:<11} {detail}")

    print()
    print("=" * 78)
    print("  READING")
    print("=" * 78)
    print("""
  A low refusal rate means the guard is a safety net, not a brake: the reviewer
  rarely reaches for a repo write, so its verification effort is unaffected.

  A HIGH rate would be a problem worth solving differently -- by giving the role a
  scratch directory outside the repo, or by relaxing the rule to "writes to the
  files the patch touches". That decision needs this measurement, not a guess.
""")


if __name__ == "__main__":
    main()
