"""List every edit a given act made, with its position in that act's call sequence.

Answers the smoke-test question precisely: did the REVISION act edit, or only
read? The revision is the executor's second act, so its calls are the tail of
the executor's sequence -- this splits them by act boundary using the tool-loop
turn count from the log, and prints what each edit actually changed.

Usage: python tools/analyze_act_edits.py <EXP> <instance> <strategy> <agent> [base_turns]
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    exp = sys.argv[1] if len(sys.argv) > 1 else "EXP-20260929-001"
    inst = sys.argv[2] if len(sys.argv) > 2 else "django__django-11001"
    strat = sys.argv[3] if len(sys.argv) > 3 else "review"
    agent = sys.argv[4] if len(sys.argv) > 4 else "executor"
    # Turns granted to the LAST act of this agent (from the cap-hit log line).
    last_act_turns = int(sys.argv[5]) if len(sys.argv) > 5 else 4

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

    total = len(calls)
    # The last act used exactly `last_act_turns` turns; earlier acts took the rest.
    boundary = max(0, total - last_act_turns)
    base, revision = calls[:boundary], calls[boundary:]

    for label, seg in (("BASE ACT", base), ("REVISION ACT", revision)):
        edits = [c for c in seg if c.get("tool") in ("edit_file", "write_file")]
        print(f"=== {label}: {len(seg)} tool calls, {len(edits)} edit(s) ===")
        for c in seg:
            tool = c.get("tool")
            args = c.get("arguments") or {}
            if tool in ("edit_file", "write_file"):
                p = args.get("path", "?")
                old = (args.get("old_string") or "")[:70].replace("\n", "\\n")
                new = (args.get("new_string") or "")[:70].replace("\n", "\\n")
                print(f"  EDIT {p}")
                print(f"       - {old}")
                print(f"       + {new}")
            else:
                p = args.get("path") or args.get("pattern") or ""
                print(f"  {tool:10s} {str(p)[:80]}")
        print()


if __name__ == "__main__":
    main()
