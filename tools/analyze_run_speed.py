"""Is a long run slow because of the MODEL, the TASK, or the BUDGET?

Three causes produce "this run is taking forever", and they call for different
responses:

  1. MODEL   -- the provider's throughput itself is low or throttled. Would make
                every run slow, and would make the curve's timings meaningless.
  2. TASK    -- the instance needs more work (more turns, bigger context). A
                property of the issue, visible at every budget level.
  3. BUDGET  -- a bigger pool lets an act keep working instead of being cut off.
                This is the intended effect of the curve, not a defect.

They are separable. Output tokens per second measures the model's generation
rate independently of how much work was queued, so if tok/s is flat while
wall-clock varies, the model is not the variable -- the workload is.

Reads the per-run summary line the runner already logs:
    ✅ 123.4s | 45678 tokens (40000 in + 5678 out) | 3 inferences | $0.01 | model=...
"""
from __future__ import annotations

import pathlib
import re
import statistics

ANSI = re.compile(r"\x1b\[[0-9;]*m")
RUN = re.compile(r"\[(\d+)/(\d+)\] Running (\w+) on (\S+)")
SUMMARY = re.compile(
    r"([\d.]+)s \| ([\d,]+) tokens \(([\d,]+) in \+ ([\d,]+) out\) \| (\d+) inferences"
)
RETRY = re.compile(r"(retry|retrying|429|rate.?limit|timeout)", re.IGNORECASE)

ROOT = pathlib.Path(__file__).resolve().parent.parent


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


def parse_log(path: pathlib.Path) -> tuple[list[dict], int]:
    """Return per-run rows and a count of retry/limit lines."""
    if not path.exists():
        return [], 0
    lines = [ANSI.sub("", ln) for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()]
    rows: list[dict] = []
    current = None
    retries = 0
    for ln in lines:
        if RETRY.search(ln):
            retries += 1
        m = RUN.search(ln)
        if m:
            current = {
                "idx": int(m.group(1)),
                "strategy": m.group(3),
                "instance": m.group(4).replace("django__django-", ""),
            }
            continue
        s = SUMMARY.search(ln)
        if s and current:
            elapsed = float(s.group(1))
            total = float(s.group(2).replace(",", ""))
            prompt = float(s.group(3).replace(",", ""))
            comp = float(s.group(4).replace(",", ""))
            if elapsed > 0:
                rows.append({
                    **current,
                    "elapsed_min": elapsed / 60,
                    "prompt": prompt,
                    "completion": comp,
                    "total": total,
                    "out_tps": comp / elapsed,
                    "in_tps": prompt / elapsed,
                    "total_tps": total / elapsed,
                })
            current = None
    return rows, retries


def main() -> int:
    dirs = level_dirs()
    if not dirs:
        print("no per_task experiments found")
        return 0

    all_rows: dict[int, list[dict]] = {}
    for lv, d in sorted(dirs.items()):
        rows, retries = parse_log(d / "logs" / "experiment.log")
        all_rows[lv] = rows
        print("=" * 96)
        print(f"LEVEL {lv}   {d.name}   {len(rows)} finished runs   "
              f"retry/limit lines in log: {retries}")
        print("=" * 96)
        print(f"{'#':>3}{'instance':<9}{'strat':<10}{'min':>7}{'prompt_tok':>12}"
              f"{'out_tok':>10}{'out tok/s':>11}{'in tok/s':>10}{'tot tok/s':>11}")
        for r in rows:
            print(f"{r['idx']:>3}{r['instance']:<9}{r['strategy']:<10}"
                  f"{r['elapsed_min']:>7.1f}{r['prompt']:>12,.0f}{r['completion']:>10,.0f}"
                  f"{r['out_tps']:>11.1f}{r['in_tps']:>10,.0f}{r['total_tps']:>11,.0f}")
        print()

    print("=" * 96)
    print("MODEL SPEED vs WORKLOAD")
    print("=" * 96)
    print(f"{'level':>6}{'runs':>6}{'median min':>12}{'median out tok/s':>19}"
          f"{'median tot tok/s':>19}{'spread out tok/s':>18}")
    for lv in sorted(all_rows):
        rows = all_rows[lv]
        if not rows:
            continue
        outs = [r["out_tps"] for r in rows]
        tots = [r["total_tps"] for r in rows]
        mins = [r["elapsed_min"] for r in rows]
        print(f"{lv:>6}{len(rows):>6}{statistics.median(mins):>12.1f}"
              f"{statistics.median(outs):>19.1f}{statistics.median(tots):>19,.0f}"
              f"{max(outs) - min(outs):>18.1f}")

    print("""
HOW TO READ THIS
  out tok/s  = completion tokens / elapsed. This is the model's GENERATION rate,
               and it is independent of how much work was queued. If it is roughly
               the same across runs while wall-clock differs by 10x, the model is
               not what makes a run long -- the amount of work is.
  in tok/s   = prompt tokens / elapsed. Prompt tokens are CUMULATIVE (every turn
               re-sends the conversation), so this rises with the turn count and
               with context size. A high value means the run processed a lot of
               context, not that the provider was slow.
  tot tok/s  = both combined; the closest thing to "throughput" for a run.

  A per-instance comparison is the decisive one: if the SAME instance is slow at
  every budget level, the cause is the task. If slowness tracks the level instead,
  the cause is the budget (intended) or throttling (not intended).
""")

    # Per-instance comparison across levels -- the decisive test.
    print("=" * 96)
    print("PER-INSTANCE WALL-CLOCK ACROSS LEVELS (minutes)")
    print("=" * 96)
    instances = sorted({r["instance"] for rows in all_rows.values() for r in rows})
    levels = sorted(all_rows)
    print(f"{'instance':<10}{'strategy':<10}" + "".join(f"{'L' + str(lv):>10}" for lv in levels))
    for inst in instances:
        for strat in ("direct", "planning", "review"):
            cells = []
            for lv in levels:
                hit = [r for r in all_rows[lv] if r["instance"] == inst and r["strategy"] == strat]
                cells.append(f"{hit[0]['elapsed_min']:>10.1f}" if hit else f"{'-':>10}")
            print(f"{inst:<10}{strat:<10}" + "".join(cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
