"""What fills the gaps in the in-flight run -- generation, or retry overhead?

A long run has two very different explanations and they need different fixes:

  GENERATION  the model is producing a lot of tokens. Legitimate work; the run
              is long because the task is big. Nothing to fix.
  OVERHEAD    the client is waiting on retries, timeouts or throttling. No tokens
              are produced during that time, so wall-clock inflates without the
              run making progress. That IS a defect worth acting on, and it would
              also mean the curve's timings partly measure the provider.

Both show up as gaps between tool calls, so they must be separated by reading
what the log says during the gaps.
"""
from __future__ import annotations

import pathlib
import re
from datetime import datetime

TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.(\d+)")
RUN = re.compile(r"\[(\d+)/(\d+)\] Running (\w+) on (\S+)")
TOOLCALL = re.compile(r"\[toolcall\] role=(\w+) tool=(\w+)")
INTERESTING = re.compile(
    r"(retry|retrying|timeout|timed out|429|rate.?limit|quota|error|failed|"
    r"empty response|no tool call|MAX-TURNS|cost)",
    re.IGNORECASE,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
d = sorted((ROOT / "results").glob("EXP-20260929*"))[-1]
log = d / "logs" / "experiment.log"
print("log:", log)

lines = [re.sub(r"\x1b\[[0-9;]*m", "", ln)
         for ln in log.read_text(encoding="utf-8", errors="ignore").splitlines()]

# Locate the last run marker, then keep only that run's lines.
start = 0
for i, ln in enumerate(lines):
    if RUN.search(ln):
        start = i
print("in-flight run lines:", len(lines) - start)

events: list[tuple[str, str]] = []
calls: list[tuple[str, str, str]] = []
for ln in lines[start:]:
    m = TS.match(ln)
    ts = f"{m.group(1)}.{m.group(2)[:3]}" if m else "?"
    tc = TOOLCALL.search(ln)
    if tc:
        calls.append((ts, tc.group(1), tc.group(2)))
    if INTERESTING.search(ln):
        body = ln.split(" - ", 1)[-1][:150]
        events.append((ts, body))

fmt = "%Y-%m-%d %H:%M:%S.%f"
print(f"\ntool calls: {len(calls)}")
if calls:
    print(f"  first {calls[0][0][-12:]}  last {calls[-1][0][-12:]}")

# Gaps between consecutive tool calls, and what the log said during each.
print("\n" + "=" * 100)
print("GAPS BETWEEN TOOL CALLS (only gaps > 60s shown)")
print("=" * 100)
total_gap = 0.0
big_gaps = 0
for i in range(len(calls) - 1):
    t0s, t1s = calls[i][0], calls[i + 1][0]
    try:
        t0 = datetime.strptime(t0s, fmt)
        t1 = datetime.strptime(t1s, fmt)
    except ValueError:
        continue
    gap = (t1 - t0).total_seconds()
    total_gap += gap
    if gap > 60:
        big_gaps += 1
        # what happened in between?
        mid = [e for e in events if t0s < e[0] < t1s]
        print(f"  {t0s[-8:]} -> {t1s[-8:]}  {gap:>6.0f}s  "
              f"after {calls[i][1]}/{calls[i][2]}")
        for ts, body in mid[:3]:
            print(f"        {ts[-8:]}  {body}")
        if not mid:
            print(f"        (nothing logged during the gap)")

try:
    span = (datetime.strptime(calls[-1][0], fmt) - datetime.strptime(calls[0][0], fmt)).total_seconds()
    print(f"\nspan {span/60:.1f} min | time in gaps {total_gap/60:.1f} min "
          f"({100*total_gap/span:.0f}%) | gaps > 60s: {big_gaps}")
except ValueError:
    pass

print("\n" + "=" * 100)
print(f"NOTABLE LOG LINES DURING THE RUN ({len(events)})")
print("=" * 100)
for ts, body in events[-40:]:
    print(f"  {ts[-8:]}  {body}")
