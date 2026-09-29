"""Loop the curve watcher, printing one compact progress line per interval.

Existed because the first monitor used a nested PowerShell loop whose quoting
broke it (it exited in under a second, having printed nothing). A Python loop
has no shell quoting to get wrong.

Usage:
    python tools/watch_curve_loop.py --interval 300
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent


def curve_levels() -> dict[int, list[dict]]:
    """Read every per_task experiment and group its runs by budget level."""
    levels: dict[int, list[dict]] = {}
    for d in sorted((ROOT / "results").glob("EXP-*")):
        yml = d / "experiment.yaml"
        if not yml.exists():
            continue
        text = yml.read_text(encoding="utf-8", errors="ignore")
        if "budget_mode: per_task" not in text:
            continue
        level = None
        for line in text.splitlines():
            if line.strip().startswith("total_tool_turns:"):
                try:
                    level = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
                break
        if level is None:
            continue
        csv_path = d / "generation_result.csv"
        if not csv_path.exists():
            continue
        with csv_path.open(newline="", encoding="utf-8", errors="ignore") as fh:
            for r in csv.DictReader(fh):
                if not r.get("instance_id"):
                    continue
                levels.setdefault(level, []).append(r)
    return levels


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    while True:
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
        levels = curve_levels()
        if not levels:
            print(f"[{stamp}] no curve runs yet", flush=True)
        else:
            parts = []
            for lv in sorted(levels):
                runs = levels[lv]
                turns = []
                for r in runs:
                    try:
                        turns.append(float(r.get("total_turns") or 0))
                    except ValueError:
                        pass
                turns.sort()
                med = turns[len(turns) // 2] if turns else 0
                trunc = sum(1 for r in runs if r.get("truncated") == "True")
                parts.append(
                    f"L{lv}: {len(runs)}/9 runs, turns med {med:.0f}, truncated {trunc}"
                )
            print(f"[{stamp}] " + " | ".join(parts), flush=True)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
