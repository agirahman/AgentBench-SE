"""One-line-per-level progress for the budget-curve sweep.

Reads each level's experiment directory and reports turns, cost, truncation and
patch status, so the sweep can be watched without grepping the run logs.

Usage:
    python tools/watch_budget_curve.py            # all levels found
    python tools/watch_budget_curve.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
from datetime import datetime, timezone

RESULTS = pathlib.Path("results")

# Which experiment directory belongs to which level is recorded INSIDE each
# run's experiment.yaml (total_tool_turns), so the mapping is read from the
# artifacts rather than assumed from directory order.
def level_of(exp_dir: pathlib.Path) -> int | None:
    yml = exp_dir / "experiment.yaml"
    if not yml.exists():
        return None
    for line in yml.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.strip().startswith("total_tool_turns:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def is_curve(exp_dir: pathlib.Path) -> bool:
    """True when this experiment used the curve's per_task configuration."""
    yml = exp_dir / "experiment.yaml"
    if not yml.exists():
        return False
    text = yml.read_text(encoding="utf-8", errors="ignore")
    return "budget_mode: per_task" in text


def read_rows(exp_dir: pathlib.Path) -> list[dict]:
    csv_path = exp_dir / "generation_result.csv"
    if not csv_path.exists():
        return []
    with csv_path.open(newline="", encoding="utf-8", errors="ignore") as fh:
        return [r for r in csv.DictReader(fh) if r.get("instance_id")]


def num(row: dict, key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    levels: dict[int, list[tuple[pathlib.Path, list[dict]]]] = {}
    for d in sorted(RESULTS.glob("EXP-*")):
        if not d.is_dir() or not is_curve(d):
            continue
        lv = level_of(d)
        if lv is None:
            continue
        levels.setdefault(lv, []).append((d, read_rows(d)))

    if not levels:
        print("no curve experiments found yet")
        return 0

    out = {}
    for lv in sorted(levels):
        runs = []
        for exp_dir, rows in levels[lv]:
            for r in rows:
                runs.append({
                    "exp": exp_dir.name,
                    "instance": r.get("instance_id", "").replace("django__django-", ""),
                    "strategy": r.get("strategy", ""),
                    "turns": num(r, "total_turns"),
                    "time_s": num(r, "execution_time"),
                    "cost_usd": num(r, "cost_usd_actual"),
                    "truncated": r.get("truncated") == "True",
                    "patch_status": r.get("patch_status", ""),
                })
        out[lv] = runs

    if args.json:
        print(json.dumps(out, indent=2))
        return 0

    now = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    print(f"BUDGET CURVE PROGRESS  ({now})")
    print("=" * 78)
    for lv in sorted(out):
        runs = out[lv]
        turns = [r["turns"] for r in runs]
        cost = sum(r["cost_usd"] for r in runs)
        trunc = sum(1 for r in runs if r["truncated"])
        valid = sum(1 for r in runs if r["patch_status"] == "VALID")
        print(f"\nLEVEL {lv}   {len(runs)}/9 runs   "
              f"turns median {sorted(turns)[len(turns)//2]:.0f}   "
              f"cost ${cost:.4f}   truncated {trunc}   valid {valid}")
        for r in sorted(runs, key=lambda x: (x["instance"], x["strategy"])):
            flag = "TRUNC" if r["truncated"] else "     "
            print(f"   {r['instance']:<7}{r['strategy']:<10}"
                  f"turns {r['turns']:>5.0f}  {r['time_s']/60:>6.1f}m  "
                  f"${r['cost_usd']:.4f}  {flag}  {r['patch_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
