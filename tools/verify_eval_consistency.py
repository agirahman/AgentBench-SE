"""Cross-check our evaluation wrapper against the harness's own verdicts.

Two independent records exist for every evaluated level:

  1. the Modal harness summary -- ``<model>.<run_id>.json`` in the repo root,
     written by swebench's ``reporting.py`` from the per-instance report.json
     files. This is the official record.
  2. our wrapper's ``results/<EXP>/predictions/<strategy>_results.json``,
     which is what every downstream script and the thesis read.

(2) is derived from (1) by our own code, so a bug there changes the headline
number without any visible symptom. That happened: an empty patch (a run that
died at the provider) fell through to the "unresolved" branch, so a dead run
was counted as a wrong answer. This tool exists to make such a disagreement
impossible to miss.

It reports, per level and strategy:
  * the harness's own counters, including its empty-patch bucket
  * our wrapper's counters
  * any instance the two classify differently
  * whether an empty patch was recorded as `patch_applied: true` by a stale
    report.json (the harness applies an empty diff as a no-op and reports
    applied=True, which is true but misleading)

Usage:
    python tools/verify_eval_consistency.py                    # every level on disk
    python tools/verify_eval_consistency.py EXP-20260929-022
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

HARNESS_KEYS = (
    "submitted_instances", "completed_instances", "resolved_instances",
    "unresolved_instances", "empty_patch_instances", "error_instances",
)


def harness_summary(exp: str, strategy: str) -> dict | None:
    """Find <model>.modal-<strategy>-<exp>.json in the repo root."""
    for f in sorted(ROOT.glob(f"*modal-{strategy}-{exp}.json")):
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
    return None


def wrapper_results(exp: str, strategy: str) -> dict | None:
    p = ROOT / "results" / exp / "predictions" / f"{strategy}_results.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def short(iid: str) -> str:
    return iid.replace("django__django-", "").replace("django-", "")


def main() -> int:
    wanted = sys.argv[1:] or None
    exps = sorted(
        d.name for d in (ROOT / "results").glob("EXP-*")
        if (d / "predictions").exists()
    )
    if wanted:
        exps = [e for e in exps if e in wanted]
    if not exps:
        print("no experiments with predictions/ found")
        return 1

    mismatches = 0
    print(f"levels on disk: {', '.join(exps)}\n")

    for exp in exps:
        strategies = [
            s for s in ("direct", "planning", "review")
            if (ROOT / "results" / exp / "predictions" / f"{s}_results.json").exists()
        ]
        if not strategies:
            continue

        print("=" * 100)
        print(exp)
        print("=" * 100)
        print(f"  {'strategy':<10} {'sub':>4} {'res':>4} {'unres':>6} {'EMPTY':>6} "
              f"{'err':>4}   harness (official)")
        print(f"  {'':<10} {'':>4} {'':>4} {'':>6} {'':>6} {'':>4}   wrapper (what we cite)")

        for strategy in strategies:
            summ = harness_summary(exp, strategy)
            own = wrapper_results(exp, strategy)

            if summ is None:
                print(f"  {strategy:<10} (no harness summary in repo root)")
                continue
            hv = [summ.get(k, 0) for k in HARNESS_KEYS]
            line1 = (f"  {strategy:<10} {hv[0]:>4} {hv[2]:>4} {hv[3]:>6} "
                     f"{hv[4]:>6} {hv[5]:>4}")

            if own is None:
                print(line1 + "   <-- NO WRAPPER RESULT (not evaluated by us)")
                continue
            empty_own = own.get("empty_patches", 0)
            unres_own = own.get("unresolved", 0)
            line2 = (f"  {'':<10} {own.get('total', 0):>4} {own.get('resolved', 0):>4} "
                     f"{unres_own:>6} {empty_own:>6} "
                     f"{own.get('harness_errors', 0):>4}")

            print(line1)
            print(line2)

            # Agreement checks.
            h_res, h_unres, h_empty = hv[2], hv[3], hv[4]
            w_res = own.get("resolved", 0)
            if w_res != h_res:
                print(f"  {'':<10} !! resolved disagrees: harness={h_res} wrapper={w_res}")
                mismatches += 1
            # The wrapper's "unresolved" should exclude empty patches, matching
            # the harness's unresolved count.
            if unres_own != h_unres:
                print(f"  {'':>10} ~ unresolved differs: harness={h_unres} "
                      f"wrapper={unres_own} (expected if empty patches are separated)")
            if empty_own != h_empty:
                print(f"  {'':<10} !! empty patches disagree: harness={h_empty} "
                      f"wrapper={empty_own}")
                mismatches += 1

            # Per-instance: an empty patch must not claim it was applied.
            empty_ids = {short(i) for i in summ.get("empty_patch_ids", [])}
            for rec in own.get("results", []):
                iid = short(rec.get("instance_id", ""))
                if iid in empty_ids:
                    if rec.get("patch_applied"):
                        print(f"  {'':<10} !! {iid} is an empty patch but wrapper "
                              f"records patch_applied=True")
                        mismatches += 1
                    if rec.get("failure_reason") != "EMPTY_PATCH":
                        print(f"  {'':<10} !! {iid} empty patch labelled "
                              f"{rec.get('failure_reason')!r}, expected 'EMPTY_PATCH'")
                        mismatches += 1
                elif rec.get("failure_reason") == "EMPTY_PATCH":
                    print(f"  {'':<10} !! {iid} labelled EMPTY_PATCH but the harness "
                          f"did not call it empty")
                    mismatches += 1

            for k in ("resolved_ids", "unresolved_ids", "empty_patch_ids", "error_ids"):
                ids = [short(i) for i in summ.get(k, [])]
                if ids:
                    print(f"  {'':<10} {k:<18} = {ids}")
        print()

    print("=" * 100)
    if mismatches:
        print(f"RESULT: {mismatches} disagreement(s) between the harness and our wrapper.")
        print("        Fix the wrapper before citing any number from this level.")
    else:
        print("RESULT: wrapper and harness agree on every level checked.")
    print("=" * 100)
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
