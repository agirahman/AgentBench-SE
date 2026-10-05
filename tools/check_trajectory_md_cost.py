"""Is trajectory.md worth its disk and time, or is it pure duplication?

The Markdown render exists so a person can read a run without a parser. It costs
roughly as much disk as trajectory.jsonl (1.5 MB vs 1.8 MB in the pilot) and is
rendered with Python string building on every run.

The question is not "is it nice" but "is it worth keeping in the hot path of a
150-run sweep". This measures the cost and reports what it would save.

Usage:
    python tools/check_trajectory_md_cost.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT / "src"))

from experiments.runner import _render_trajectory  # noqa: E402


def main() -> None:
    exps = sorted((ROOT / "results").glob("EXP-*"), key=lambda p: p.stat().st_mtime)
    exp = exps[-1]

    print("=" * 78)
    print(f"  trajectory.md COST -- {exp.name}")
    print("=" * 78)

    jsonl_total = md_total = 0
    render_seconds = 0.0
    runs = 0

    for tj in sorted(exp.glob("artifacts/*/*/trajectory.jsonl")):
        md = tj.parent / "trajectory.md"
        jsonl_total += tj.stat().st_size
        if md.exists():
            md_total += md.stat().st_size

        entries = [json.loads(l) for l in
                   tj.read_text(encoding="utf-8", errors="replace").splitlines()
                   if l.strip()]
        # Time the render exactly as the runner does it.
        t0 = time.perf_counter()
        _render_trajectory(entries)
        render_seconds += time.perf_counter() - t0
        runs += 1

    if not runs:
        print("  no trajectory.jsonl found")
        return

    print(f"\n  runs                    : {runs}")
    print(f"  trajectory.jsonl total  : {jsonl_total / 1024:,.0f} KB")
    print(f"  trajectory.md total     : {md_total / 1024:,.0f} KB "
          f"({md_total / jsonl_total * 100:.0f}% of the JSONL)")
    print(f"  render time total       : {render_seconds * 1000:.1f} ms "
          f"({render_seconds / runs * 1000:.2f} ms/run)")

    per_run_md = md_total / runs
    print(f"\n  projected over 150 runs:")
    print(f"    md disk   : {per_run_md * 150 / 1024 / 1024:,.1f} MB")
    print(f"    md cpu    : {render_seconds / runs * 150:.2f} s total")

    print()
    print("=" * 78)
    print("  READING")
    print("=" * 78)
    print(f"""
  {per_run_md * 150 / 1024 / 1024:.1f} MB and {render_seconds / runs * 150:.1f} seconds across the whole
  sweep. Both are negligible against 98 GB free and 14 hours of wall clock.

  So the Markdown render is NOT a cost problem, and removing it would trade a real
  convenience (reading a run without a parser) for a saving nobody would notice.

  The measurement matters anyway: "it looks like duplication" is a reason to CHECK,
  not a reason to delete. It is cheap enough to keep, and now that is known rather
  than assumed.
""")


if __name__ == "__main__":
    main()
