"""Re-project the sweep from MEASURED per-run times, now that level 100 runs.

The earlier estimate assumed a run grows with the pool but stays near the old
durations. Level 100 disproves that: 11019/planning is at 65+ minutes and 86
tool calls. Per-run time is now the dominant term, so project from data.
"""
import pathlib
import re
from datetime import datetime

TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+")
RUN = re.compile(r"\[(\d+)/(\d+)\] Running (\w+) on (\S+)")

root = pathlib.Path("results")


def level_dirs() -> dict[int, pathlib.Path]:
    out = {}
    for d in sorted(root.glob("EXP-20260929*")):
        y = d / "experiment.yaml"
        if not y.exists():
            continue
        t = y.read_text(encoding="utf-8", errors="ignore")
        if "budget_mode: per_task" not in t:
            continue
        m = re.search(r"total_tool_turns:\s*(\d+)", t)
        if m:
            out[int(m.group(1))] = d
    return out


def run_times(d: pathlib.Path) -> list[float]:
    """Minutes per run, derived from the log's run markers."""
    log = d / "logs" / "experiment.log"
    if not log.exists():
        return []
    lines = log.read_text(encoding="utf-8", errors="ignore").splitlines()
    marks = []
    for i, line in enumerate(lines):
        m = RUN.search(line)
        if m:
            t = TS.match(line)
            marks.append((i, t.group(1) if t else None, m.group(3), m.group(4)))
    out = []
    now = datetime.now()
    for j, (idx, start, strat, inst) in enumerate(marks):
        end_line = marks[j + 1][0] if j + 1 < len(marks) else len(lines)
        end = None
        for k in range(end_line - 1, idx, -1):
            t = TS.match(lines[k])
            if t:
                end = t.group(1)
                break
        if not start:
            continue
        try:
            t0 = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
            t1 = datetime.strptime(end, "%Y-%m-%d %H:%M:%S") if end else now
            out.append((t1 - t0).total_seconds() / 60)
        except ValueError:
            continue
    return out


print("=" * 78)
print("MEASURED RUN TIMES (minutes)")
print("=" * 78)
for lv, d in sorted(level_dirs().items()):
    times = run_times(d)
    if not times:
        print(f"  L{lv}: no timing data ({d.name})")
        continue
    done = times[:-1] if len(times) < 9 else times
    print(f"  L{lv}  {d.name}  {len(times)} runs seen")
    print(f"      median {sorted(done)[len(done)//2]:.1f} min   max {max(times):.1f} min   "
          f"sum {sum(times):.1f} min")

# Projection: assume level 200 runs scale with the pool the way 40 -> 100 did.
print()
print("=" * 78)
print("PROJECTION FOR LEVEL 200")
print("=" * 78)
l40 = run_times(level_dirs().get(40, pathlib.Path(".")))
l100 = run_times(level_dirs().get(100, pathlib.Path(".")))
med40 = sorted(l40)[len(l40) // 2] if l40 else 0
med100 = sorted(l100)[len(l100) // 2] if l100 else 0
print(f"  median run: L40 {med40:.1f} min, L100 {med100:.1f} min")
if med40 and med100:
    ratio = med100 / med40
    print(f"  growth factor per 2.5x pool: {ratio:.2f}x")
    print(f"  projected L200 median run: {med100 * ratio:.1f} min "
          f"-> 9 runs = {med100 * ratio * 9 / 60:.1f} h")
    print(f"  (worst case: if L200 runs behave like the slowest L100 run "
          f"({max(l100):.0f} min), 9 runs = {max(l100) * 9 / 60:.1f} h)")
