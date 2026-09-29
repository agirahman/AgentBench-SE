"""Per-run timing for the in-flight level, to tell "slow" from "stuck".

Reads the level's experiment.log, finds each "[n/9] Running <strategy> on
<instance>" marker with its timestamp, and reports how long each run took or
has been taking, plus tool-call activity for the one still in flight.
"""
import pathlib
import re
import sys
from datetime import datetime

# find the level-100 experiment dir
root = pathlib.Path("results")
lvl_dir = None
for d in sorted(root.glob("EXP-20260929*")):
    y = d / "experiment.yaml"
    if y.exists() and re.search(r"total_tool_turns:\s*100", y.read_text(encoding="utf-8", errors="ignore")):
        lvl_dir = d
        break

if lvl_dir is None:
    # no yaml yet (still running with old code path?) -> newest dir
    dirs = sorted(root.glob("EXP-20260929*"))
    lvl_dir = dirs[-1] if dirs else None
print("level dir:", lvl_dir.name if lvl_dir else "NONE")
if lvl_dir is None:
    sys.exit(0)

log = lvl_dir / "logs" / "experiment.log"
text = log.read_text(encoding="utf-8", errors="ignore")
lines = text.splitlines()

TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+")
RUN = re.compile(r"\[(\d+)/(\d+)\] Running (\w+) on (\S+)")

marks = []
for i, line in enumerate(lines):
    m = RUN.search(line)
    if m:
        t = TS.match(line)
        marks.append({
            "idx": int(m.group(1)),
            "of": int(m.group(2)),
            "strategy": m.group(3),
            "instance": m.group(4).replace("django__django-", ""),
            "start": t.group(1) if t else "?",
            "line": i,
        })

print(f"\nruns found in log: {len(marks)}")
print(f"{'#':>3}{'strategy':<10}{'instance':<9}{'started':>10}{'ended':>10}{'elapsed':>10}")
now = datetime.now()
for j, mk in enumerate(marks):
    # end = timestamp of the next run's marker, or the last log line
    if j + 1 < len(marks):
        end_line = marks[j + 1]["line"]
    else:
        end_line = len(lines)
    end_ts = None
    for k in range(end_line - 1, mk["line"], -1):
        t = TS.match(lines[k])
        if t:
            end_ts = t.group(1)
            break
    try:
        t0 = datetime.strptime(mk["start"], "%Y-%m-%d %H:%M:%S")
        t1 = datetime.strptime(end_ts, "%Y-%m-%d %H:%M:%S") if end_ts else now
        mins = (t1 - t0).total_seconds() / 60
    except Exception:
        mins = float("nan")
    running = "  <== IN FLIGHT" if j == len(marks) - 1 else ""
    print(f"{mk['idx']:>3}{mk['strategy']:<10}{mk['instance']:<9}"
          f"{mk['start'][-8:]:>10}{(end_ts or '...')[-8:]:>10}{mins:>9.1f}m{running}")

# tool calls in the in-flight run
cur = marks[-1] if marks else None
if cur:
    tc = lvl_dir / "artifacts" / f"django__django-{cur['instance']}" / cur["strategy"] / "tool_calls.jsonl"
    print(f"\nin-flight run artifacts: {tc.exists()}")
    if tc.exists():
        import json
        from collections import Counter
        calls = [json.loads(l) for l in tc.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]
        print(f"  tool calls so far: {len(calls)}")
        print(f"  by agent: {dict(Counter(c['agent'] for c in calls))}")
        print(f"  by tool:  {dict(Counter(c['tool'] for c in calls))}")
        if calls:
            print(f"  last call: {calls[-1]['tool']} by {calls[-1]['agent']}")
