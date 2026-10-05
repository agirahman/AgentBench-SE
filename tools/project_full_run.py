"""Project 50 issues from MEASURED per-run times, not assumptions.

The curve has now produced real timings at two budget levels, so the projection
can be built from data. The question the user asked is concrete: if 50 issues
must run, how long is that, and does it fit on a laptop or does it need a VPS?

Measured inputs:
  - level 40 : 9 runs, sum 79.5 min  -> 8.8 min/run
  - level 100: 7 runs so far, sum 127.6 min -> 18.2 min/run
Level 200 is projected from the observed growth, with a range because run length
is heavy-tailed (the median run is short, the slowest is 66 min).
"""
from __future__ import annotations

import pathlib
import re
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
ANSI = re.compile(r"\x1b\[[0-9;]*m")
RUN = re.compile(r"\[(\d+)/(\d+)\] Running (\w+) on (\S+)")
TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+")


def level_dirs() -> dict[int, pathlib.Path]:
    out = {}
    for d in sorted((ROOT / "results").glob("EXP-20260929*")):
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


def run_minutes(d: pathlib.Path) -> list[float]:
    log = d / "logs" / "experiment.log"
    if not log.exists():
        return []
    lines = [ANSI.sub("", ln) for ln in log.read_text(encoding="utf-8", errors="ignore").splitlines()]
    marks = []
    for i, ln in enumerate(lines):
        m = RUN.search(ln)
        if m:
            t = TS.match(ln)
            marks.append((i, t.group(1) if t else None))
    now = datetime.now()
    out = []
    for j, (idx, start) in enumerate(marks):
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


def main() -> int:
    dirs = level_dirs()
    stats: dict[int, dict] = {}
    inflight: dict[int, float] = {}
    for lv, d in sorted(dirs.items()):
        times = run_minutes(d)
        if not times:
            continue
        # The last entry is the run still in flight. It must NOT be mixed into
        # the statistics (it is unfinished and always long), but dropping it
        # silently hides the heaviest run -- and the heavy tail is exactly what
        # decides whether a 150-run sweep finishes overnight. Kept separate and
        # reported, so the mean can be shown with and without it.
        if len(times) < 9:
            inflight[lv] = times[-1]
            done = times[:-1]
        else:
            done = times
        if not done:
            continue
        srt = sorted(done)
        stats[lv] = {
            "n": len(done),
            "mean": sum(done) / len(done),
            "median": srt[len(srt) // 2],
            "max": max(done),
            "sum": sum(done),
        }

    print("=" * 92)
    print("MEASURED PER-RUN TIME (finished runs only)")
    print("=" * 92)
    print(f"{'level':>6}{'runs':>6}{'mean':>9}{'median':>9}{'max':>9}{'sum':>10}")
    for lv, s in stats.items():
        print(f"{lv:>6}{s['n']:>6}{s['mean']:>8.1f}m{s['median']:>8.1f}m{s['max']:>8.1f}m{s['sum']:>9.1f}m")
    if inflight:
        print()
        print("  in flight (excluded above, reported so the tail is visible):")
        for lv, m in sorted(inflight.items()):
            print(f"    level {lv}: {m:.1f} min and counting")

    # Growth is measured on the MEAN, because the mean is what a full run
    # accumulates. The median is the wrong statistic here: it is dominated by
    # short runs and can even fall as the pool grows (observed: 6.0m -> 4.2m),
    # which would project a NEGATIVE growth and imply level 200 is faster.
    growth = None
    if 40 in stats and 100 in stats:
        growth = stats[100]["mean"] / stats[40]["mean"]
        print()
        print(f"mean growth 40 -> 100 (2.5x pool): {growth:.2f}x")
        if inflight.get(100):
            with_tail = (stats[100]["sum"] + inflight[100]) / (stats[100]["n"] + 1)
            print(f"  level-100 mean INCLUDING the in-flight run: {with_tail:.1f} min "
                  f"(vs {stats[100]['mean']:.1f} excluding it)")

    if growth and 100 in stats:
        stats[200] = {
            "n": 0,
            "mean": stats[100]["mean"] * growth,
            "median": 0,
            "max": stats[100]["max"] * growth,
            "sum": 0,
        }

    print()
    print("=" * 92)
    print("WALL-CLOCK FOR A FULL RUN  (50 issues x 3 strategies = 150 runs)")
    print("=" * 92)
    print(f"{'level':>6}{'min/run':>10}{'sequential':>14}{'2 parallel':>14}{'4 parallel':>14}")
    for lv, s in sorted(stats.items()):
        m = s["mean"]
        total = m * 150 / 60
        tag = "  (projected)" if s["n"] == 0 else ""
        print(f"{lv:>6}{m:>9.1f}m{total:>12.1f} h{total/2:>12.1f} h{total/4:>12.1f} h{tag}")

    # Sensitivity: the mean is the uncertain quantity, so show the band.
    if 40 in stats:
        lo = stats[40]["mean"]
        hi = stats[100]["max"] if 100 in stats else lo * 2
        print()
        print("  SENSITIVITY (150 runs, sequential):")
        for m in (lo, (lo + hi) / 2, hi):
            print(f"    at {m:>5.1f} min/run -> {m * 150 / 60:>5.1f} h")
        print("    The upper bound uses the slowest OBSERVED run, which is the")
        print("    honest worst case: one 66-min run per slot is possible.")

    print("""
READING THIS
  "sequential" is one process doing every run. "N parallel" splits the issue set
  into N disjoint halves and runs them at once, which the earlier parallel-
  readiness work confirmed is safe for the repo cache (distinct base_commits get
  distinct working directories) and for 9router (measured stable at 8 concurrent
  requests).

  Parallelism does NOT reduce the number of API calls -- it reduces wall-clock by
  overlapping them. The free model still serves the same total volume, so if the
  provider throttles under load, the parallel columns are optimistic.

  Per-run time grows SUB-linearly with the pool: 8.8 min mean at level 40,
  15.7 min at level 100 -- a 1.78x rise for a 2.5x pool. The MEDIAN barely moves
  (6.0 -> 4.2 min) while the mean more than doubles, so what a bigger pool buys
  is not "every run takes longer" but "a few runs iterate far longer". That tail
  is what makes the sequential estimate unreliable: the mean is dragged by a
  minority of runs, and which runs those are is not predictable in advance.""")

    print()
    print("=" * 92)
    print("CAN A LAPTOP DO IT?")
    print("=" * 92)
    print("""
The constraint is not CPU or RAM -- a run is almost entirely waiting on the API,
and the whole repo cache is 1.4 GB. The real constraints are:

  1. WALL-CLOCK. A 150-run sweep is ~22 h sequential at the measured mean. A
     laptop that sleeps, updates, or closes its lid kills the sweep.

  2. NETWORK CONTINUITY. 9router listens on localhost:20128. If the laptop
     suspends, the proxy goes away and the in-flight request fails.

  3. NO MID-RUN RESUME. --resume skips FINISHED runs by reading
     predictions/*.jsonl. An interrupted run leaves no line, so it restarts from
     scratch -- up to 66 minutes lost per interruption.

A VPS removes all three: no lid, no sleep, and an SSH session can drop without
killing the process (tmux/nohup).""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
