"""Where the budget-curve sweep stands, and whether each level produced usable data.

One command instead of the several one-off scripts that were written while the
sweep was running. Reports, per level:

  * whether the experiment directory and its config exist at all
  * how many of the 9 runs produced a prediction
  * whether any patch is EMPTY (a distinct outcome from a wrong patch -- the
    SWE-bench harness counts empty patches separately, and so should we)
  * provider errors that killed a run, and truncation, from the CSV
  * the sweep log's own timing lines, so an aborted level is visible

A level that "finished rc=0 in 1.9 min" did not run: nine runs cannot finish that
fast. This tool makes that visible instead of leaving a plausible-looking row.
"""
from __future__ import annotations

import csv
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def read_tolerant(path: pathlib.Path) -> str:
    raw = path.read_bytes()
    if raw[:2] == b"\xff\xfe":
        return raw.decode("utf-16", errors="ignore")
    return raw.decode("utf-8", errors="ignore")


def main() -> int:
    print("=" * 100)
    print("LEVELS ON DISK")
    print("=" * 100)
    rows = []
    for d in sorted((ROOT / "results").glob("EXP-20260929*")):
        y = d / "experiment.yaml"
        pool = mode = "?"
        if y.exists():
            t = y.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"total_tool_turns:\s*(\d+)", t)
            pool = m.group(1) if m else "?"
            m = re.search(r"budget_mode:\s*(\S+)", t)
            mode = m.group(1) if m else "?"
        preds = d / "predictions" / "predictions.jsonl"
        n_pred = len([l for l in preds.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]) if preds.exists() else 0
        csv_p = d / "generation_result.csv"
        rows.append((pool, d, mode, n_pred, csv_p))

    seen: set[str] = set()
    for pool, d, mode, n_pred, csv_p in rows:
        seen.add(pool)
        print(f"  pool={pool:<5} {d.name}  mode={mode:<9} predictions={n_pred}/9  "
              f"csv={'yes' if csv_p.exists() else 'no'}  "
              f"artifacts={len(list((d/'artifacts').glob('*'))) if (d/'artifacts').exists() else 0}")

    for want in ("40", "100", "200"):
        if want not in seen:
            print(f"  pool={want:<5} <no experiment directory>  <-- this level produced no data")

    print()
    print("=" * 100)
    print("EMPTY PATCHES AND ERRORS (from each level's CSV)")
    print("=" * 100)
    for pool, d, mode, n_pred, csv_p in rows:
        if not csv_p.exists():
            continue
        print(f"\n  {d.name} (pool {pool})")
        with csv_p.open(encoding="utf-8", errors="ignore", newline="") as fh:
            rd = csv.DictReader(fh)
            for row in rd:
                iid = (row.get("instance_id") or "?").replace("django__django-", "")
                status = row.get("patch_status", "")
                err = (row.get("error") or "")[:90]
                trunc = row.get("truncated", "")
                flag = ""
                if status not in ("VALID", ""):
                    flag = "  <== " + status
                if err:
                    flag += f"  err={err!r}"
                print(f"    {iid:<10} {row.get('strategy',''):<9} status={status:<10} "
                      f"trunc={trunc:<6} turns={row.get('api_turns','')}{flag}")

    print()
    print("=" * 100)
    print("EMPTY PATCHES IN predictions.jsonl")
    print("=" * 100)
    for pool, d, mode, n_pred, csv_p in rows:
        preds = d / "predictions" / "predictions.jsonl"
        if not preds.exists():
            continue
        for line in preds.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            j = json.loads(line)
            patch = j.get("model_patch") or ""
            if not patch.strip():
                print(f"  {d.name}  {(j.get('instance_id') or '?').replace('django__django-','')}  "
                      f"EMPTY PATCH")

    print()
    print("=" * 100)
    print("SWEEP LOG TIMING")
    print("=" * 100)
    sweep = ROOT / "logs" / "budget_curve_sweep.log"
    if not sweep.exists():
        print("  (no logs/budget_curve_sweep.log)")
        return 0
    for ln in read_tolerant(sweep).splitlines():
        if re.search(r"level \d+ finished|SWEEP DONE|Health check failed|LEVEL \d+", ln):
            print(f"  {ln.strip()[:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
