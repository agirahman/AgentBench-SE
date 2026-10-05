"""Per-act tool-turn usage across the EXP-20260928-003 review/planning runs.

Reads tool_calls.jsonl (one record per tool call, with the act role) to answer:
where did the 40-turn pool actually go, and how much did each act leave unused?

The point: if the planner and reviewer consistently stop far below their share
while the executor hits its cap, the even split (13/13/14) is the wrong shape --
the executor, which writes the code, is the act that needs the turns.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
EXP = ROOT / "results/EXP-20260928-003/artifacts"


def main() -> None:
    for strategy in ("planning", "review"):
        print("=" * 78)
        print(f"{strategy.upper()}")
        print("=" * 78)
        per_role: dict[str, list[int]] = defaultdict(list)
        instances = sorted(p.name for p in EXP.iterdir() if p.is_dir())
        for inst in instances:
            tc = EXP / inst / strategy / "tool_calls.jsonl"
            if not tc.exists():
                continue
            counts: dict[str, int] = defaultdict(int)
            for line in tc.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                counts[rec.get("agent", "?")] += 1
            if counts:
                for role, n in counts.items():
                    per_role[role].append(n)
                print(f"  {inst:28s} " + "  ".join(f"{r}={n}" for r, n in sorted(counts.items())))
        print("-" * 78)
        for role, vals in sorted(per_role.items()):
            if not vals:
                continue
            vals_sorted = sorted(vals)
            median = vals_sorted[len(vals_sorted) // 2]
            print(
                f"  {role:12s} n={len(vals):2d}  min={min(vals):3d}  "
                f"median={median:3d}  max={max(vals):3d}  total={sum(vals):4d}"
            )
        print()


if __name__ == "__main__":
    main()
