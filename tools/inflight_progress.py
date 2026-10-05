"""Count tool calls for the IN-FLIGHT run from the experiment log.

Artifacts (tool_calls.jsonl, messages.jsonl) are written only when a run
FINISHES, so they cannot show progress for a run still going. The log streams
every tool call as it happens, so it is the only live source.

Level 100 lets one act draw ~100 turns, so a long run is expected to be long --
the question this answers is whether it is still making progress or wedged.
"""
import pathlib
import re
from collections import Counter
from datetime import datetime

TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+")
RUN = re.compile(r"\[(\d+)/(\d+)\] Running (\w+) on (\S+)")
TOOLCALL = re.compile(r"\[toolcall\] role=(\w+) tool=(\w+)")

root = pathlib.Path("results")
lvl_dir = None
for d in sorted(root.glob("EXP-20260929*")):
    y = d / "experiment.yaml"
    if y.exists() and re.search(r"total_tool_turns:\s*100", y.read_text(encoding="utf-8", errors="ignore")):
        lvl_dir = d
        break
if lvl_dir is None:
    lvl_dir = sorted(root.glob("EXP-20260929*"))[-1]
print("level dir:", lvl_dir.name)

log = lvl_dir / "logs" / "experiment.log"
lines = log.read_text(encoding="utf-8", errors="ignore").splitlines()

# find the last run marker
last_mark = 0
last_run = None
for i, line in enumerate(lines):
    m = RUN.search(line)
    if m:
        last_mark = i
        last_run = m
print(f"last run marker: #{last_run.group(1)} {last_run.group(3)} on "
      f"{last_run.group(4).replace('django__django-','')} at line {last_mark}")

calls = []
for line in lines[last_mark:]:
    m = TOOLCALL.search(line)
    if m:
        t = TS.match(line)
        calls.append((t.group(1) if t else "?", m.group(1), m.group(2)))

print(f"\ntool calls in this run: {len(calls)}")
print(f"  by agent: {dict(Counter(a for _, a, _ in calls))}")
print(f"  by tool:  {dict(Counter(t for _, _, t in calls))}")

if calls:
    print(f"\n  first call : {calls[0][0][-8:]}  {calls[0][1]}/{calls[0][2]}")
    print(f"  last  call : {calls[-1][0][-8:]}  {calls[-1][1]}/{calls[-1][2]}")

    # gap analysis: is it still moving?
    fmt = "%Y-%m-%d %H:%M:%S"
    try:
        t0 = datetime.strptime(calls[0][0], fmt)
        t1 = datetime.strptime(calls[-1][0], fmt)
        print(f"  span       : {(t1 - t0).total_seconds()/60:.1f} min")
    except ValueError:
        pass

    # per-minute rate over the last 10 calls
    try:
        recent = [datetime.strptime(c[0], fmt) for c in calls[-10:]]
        gaps = [(recent[i+1] - recent[i]).total_seconds() for i in range(len(recent)-1)]
        if gaps:
            print(f"  last 10 calls: avg gap {sum(gaps)/len(gaps):.0f}s, "
                  f"max {max(gaps):.0f}s")
    except ValueError:
        pass

    print("\n  edits so far:")
    for i, (ts, agent, tool) in enumerate(calls):
        if tool in ("edit_file", "write_file"):
            print(f"    #{i:>3} {ts[-8:]} {agent}")

    print(f"\n  last 5 calls:")
    for ts, agent, tool in calls[-5:]:
        print(f"    {ts[-8:]} {agent:<10} {tool}")

# process still alive?
import subprocess
try:
    out = subprocess.run(["tasklist", "/FI", "PID eq 2204"], capture_output=True, text=True, timeout=20)
    alive = "2204" in out.stdout
except Exception:
    alive = None
print(f"\nsweep PID 2204 alive: {alive}")
