"""Show the tool-call sequence of one act, to tell "cut off mid-work" from "wrapped up".

If the final calls of a capped act are still exploration (read_file/grep/list_files)
the act was interrupted; if they are edits followed by a summary, it had converged.

Usage: python tools/_act_trace.py <EXP> <instance> <strategy> [agent]
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    exp = sys.argv[1] if len(sys.argv) > 1 else "EXP-20260928-003"
    inst = sys.argv[2] if len(sys.argv) > 2 else "django__django-10924"
    strat = sys.argv[3] if len(sys.argv) > 3 else "review"
    agent = sys.argv[4] if len(sys.argv) > 4 else "executor"

    path = ROOT / f"results/{exp}/artifacts/{inst}/{strat}/tool_calls.jsonl"
    if not path.exists():
        print(f"missing {path}")
        return

    calls = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("agent") == agent:
            calls.append(rec)

    print(f"{inst} / {strat} / {agent}: {len(calls)} tool calls")
    print(f"  mix: {dict(Counter(c.get('tool') for c in calls))}\n")
    print("  last 8 calls:")
    for rec in calls[-8:]:
        args = rec.get("arguments") or {}
        brief = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in list(args.items())[:2])
        print(f"    {rec.get('tool'):10s} {brief}")
    print("\n  edits (edit_file/write_file) at positions:")
    edits = [i for i, c in enumerate(calls) if c.get("tool") in ("edit_file", "write_file")]
    print(f"    {edits if edits else 'none'}")


if __name__ == "__main__":
    main()
