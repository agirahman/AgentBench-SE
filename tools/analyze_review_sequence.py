"""Print the message sequence of one review run, to locate the revision boundary.

The revision is not a separate agent: the reviewer returns NEEDS_REVISION and the
EXECUTOR runs a second act on the feedback. So "did the revision work?" means
inspecting the executor's SECOND act, which this script makes visible.

Usage: python tools/analyze_review_sequence.py <EXP> <instance> [strategy]
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

    path = ROOT / f"results/{exp}/artifacts/{inst}/{strat}/messages.jsonl"
    if not path.exists():
        print(f"missing {path}")
        return

    print(f"{inst} / {strat}\n")
    verdicts: list[str] = []
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = (d.get("content") or "").replace("\n", " ")
        sender = d.get("sender", "?")
        receiver = d.get("receiver", "?")
        kind = d.get("kind", "?")
        ops = ",".join(d.get("bb_ops") or [])
        preview = content[:100]
        print(f"{i:3d} {sender:12s} -> {receiver:12s} [{kind:10s}] {ops:28s} {preview}")
        if "verdict" in content:
            verdicts.append(content[:160])

    print("\n--- verdicts found ---")
    for v in verdicts:
        print(f"  {v}")


if __name__ == "__main__":
    main()
