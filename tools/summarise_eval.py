"""Summarise a Modal evaluation across the three strategies.

Reads <exp>/predictions/<strategy>_results.json, which eval_modal.py writes next to
the predictions it graded (not at the experiment root).

Usage:
    python tools/summarise_eval.py --exp EXP-20260930-415
    python tools/summarise_eval.py                       # newest experiment
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STRATEGIES = ("direct", "planning", "review")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exp", default=None)
    args = ap.parse_args()

    exp = (ROOT / "results" / args.exp if args.exp
           else sorted((ROOT / "results").glob("EXP-*"), key=lambda p: p.stat().st_mtime)[-1])

    print("=" * 78)
    print(f"  {exp.name} -- MODAL EVALUATION")
    print("=" * 78)

    summary: dict[str, dict] = {}
    for strat in STRATEGIES:
        path = exp / "predictions" / f"{strat}_results.json"
        if not path.exists():
            continue
        summary[strat] = json.loads(path.read_text(encoding="utf-8"))

    if not summary:
        print("\n  no results files found under predictions/")
        return

    print()
    print(f"  {'strategy':<10} {'resolved':>9} {'graded':>7} {'empty':>6} "
          f"{'errors':>7} {'rate':>7}")
    print(f"  {'-' * 52}")
    for strat, d in summary.items():
        print(f"  {strat:<10} {d.get('resolved', '?'):>4}/{d.get('total', '?'):<4} "
              f"{d.get('graded', '?'):>7} {d.get('empty_patches', '?'):>6} "
              f"{d.get('harness_errors', '?'):>7} "
              f"{d.get('success_rate_graded', 0):>6.0f}%")

    # Per-instance view, so a single instance cannot hide behind the total.
    instances: list[str] = []
    for d in summary.values():
        for r in d.get("results") or []:
            iid = r.get("instance_id")
            if iid and iid not in instances:
                instances.append(iid)

    if instances:
        print()
        print("  per instance:")
        header = "  " + " " * 26 + "  ".join(f"{s:>8}" for s in summary)
        print(header)
        for iid in instances:
            row = []
            for strat in summary:
                rec = next((r for r in (summary[strat].get("results") or [])
                            if r.get("instance_id") == iid), None)
                if rec is None:
                    row.append(f"{'-':>8}")
                else:
                    row.append(f"{'PASS' if rec.get('resolved') else 'fail':>8}")
            print(f"  {iid:<26}  " + "  ".join(row))

    print()
    print("=" * 78)
    print("  NOTES")
    print("=" * 78)
    for strat, d in summary.items():
        if not d.get("success_rate_is_valid", True):
            print(f"  {strat}: success_rate marked INVALID by the harness")
        if d.get("empty_patches"):
            print(f"  {strat}: {d['empty_patches']} empty patch(es) -- these are "
                  f"no-answer outcomes, not strategy failures")
        if d.get("harness_errors"):
            print(f"  {strat}: {d['harness_errors']} harness error(s) -- "
                  f"infrastructure, not the strategy")
    print("=" * 78)


if __name__ == "__main__":
    main()
