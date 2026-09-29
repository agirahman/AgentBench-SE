"""Print the full reviewer verdicts and the revision's stated change for one run.

Answers: WHY did the first review reject, and did the revision address it?
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

    p = ROOT / f"results/{exp}/artifacts/{inst}/{strat}/messages.jsonl"
    for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        sender = d.get("sender")
        if sender not in ("reviewer", "executor"):
            continue
        c = d.get("content") or ""
        print("=" * 100)
        print(f"MSG {i}  sender={sender}  receiver={d.get('receiver')}  ops={d.get('bb_ops')}")
        print("=" * 100)
        print(c[:2000])
        print()


if __name__ == "__main__":
    main()
