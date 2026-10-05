"""Read the in-flight log without locking it (the runner holds it open).

Answers one question: are the model's responses being CUT OFF at max_tokens?
A truncated response is a different failure mode from "thinking too long" --
the model never gets to finish its thought, and no budget increase fixes it,
because the limit is per-response, not per-task.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
d = sorted((ROOT / "results").glob("EXP-20260929*"))[-1]
log = d / "logs" / "experiment.log"

text = ""
with log.open("r", encoding="utf-8", errors="ignore") as fh:
    for line in fh:
        text += line
text = re.sub(r"\x1b\[[0-9;]*m", "", text)
lines = text.splitlines()
print(f"log lines: {len(lines)}")

PATTERNS = ("finish_reason", "length", "truncat", "max_tokens", "MAX-TURNS",
            "cost", "retryable", "empty")
print("\n" + "=" * 90)
print("LINES MENTIONING LIMITS / TRUNCATION / RETRIES")
print("=" * 90)
hits = 0
for ln in lines:
    low = ln.lower()
    if any(p.lower() in low for p in PATTERNS):
        hits += 1
        print("  " + ln.strip()[:190])
print(f"\n  total: {hits}")

# Turn numbers reached, per role -- shows how deep the loops got.
print("\n" + "=" * 90)
print("HIGHEST TURN NUMBER PER ROLE")
print("=" * 90)
turn = re.compile(r"tool_loop\[(\w+)\].*?turn (\d+)")
best: dict[str, int] = {}
for ln in lines:
    m = turn.search(ln)
    if m:
        role, n = m.group(1), int(m.group(2))
        best[role] = max(best.get(role, 0), n)
for role, n in sorted(best.items()):
    print(f"  {role:<12} highest turn seen: {n}")

# Anything that looks like an error.
print("\n" + "=" * 90)
print("ERROR-LIKE LINES")
print("=" * 90)
err = re.compile(r"(error|exception|failed|traceback)", re.IGNORECASE)
n = 0
for ln in lines:
    if err.search(ln) and "[toolcall]" not in ln:
        n += 1
        if n <= 15:
            print("  " + ln.strip()[:190])
print(f"\n  total: {n}")
