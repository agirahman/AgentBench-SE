"""Attribute each cap hit in an experiment log to the act that hit it.

The warning alone does not say WHICH act was capped: review's executor runs
twice (base and revision). This pairs every cap hit with the act boundary by
looking at the tool-loop start/finish lines and the message log timestamps, so
"the revision was cut off" can be distinguished from "the base act was cut off".

Usage: python tools/analyze_cap_attribution.py <EXP> <instance> <strategy>
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    exp = sys.argv[1] if len(sys.argv) > 1 else "EXP-20260929-001"
    inst = sys.argv[2] if len(sys.argv) > 2 else "django__django-11001"
    strat = sys.argv[3] if len(sys.argv) > 3 else "review"

    log = ROOT / f"results/{exp}/logs/experiment.log"
    if not log.exists():
        print(f"missing {log}")
        return

    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()

    # Act boundaries: the orchestrator's task messages mark each act, and the
    # cap warnings sit inside one of those windows.
    current = "?"
    events: list[tuple[str, str]] = []
    for ln in lines:
        ts = ln.split("|")[0].strip()[:19] if "|" in ln else ""
        m = re.search(r"\[toolcall\] role=(\w+) tool=(\w+)", ln)
        if m:
            events.append((ts, f"  toolcall {m.group(1)} {m.group(2)}"))
        c = re.search(r"hit max_tool_turns=(\d+) for role=(\w+)", ln)
        if c:
            events.append((ts, f">>> CAP HIT max_tool_turns={c.group(1)} role={c.group(2)}"))
        for marker, label in (
            ("Running review strategy", "ACT-START review strategy"),
            ("revision", "mentions revision"),
        ):
            if marker in ln:
                events.append((ts, f"--- {label}: {ln.split(' - ')[-1][:90]}"))

    print(f"{inst} / {strat} — {len(events)} events\n")
    for ts, text in events[-40:]:
        print(f"{ts}  {text}")

    # Message log gives the act sequence with timestamps.
    msg = ROOT / f"results/{exp}/artifacts/{inst}/{strat}/messages.jsonl"
    if msg.exists():
        print("\n--- message sequence (act boundaries) ---")
        for i, line in enumerate(msg.read_text(encoding="utf-8", errors="replace").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("kind") == "task":
                ts = (d.get("timestamp") or "")[:19]
                ops = ",".join(d.get("bb_ops") or [])
                print(f"  {i:3d} {ts}  {d.get('receiver'):10s} <- task  ops={ops}")


if __name__ == "__main__":
    main()
