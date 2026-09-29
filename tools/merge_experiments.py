#!/usr/bin/env python
"""Merge two experiment output directories into one combined directory.

Merges:
  - generation_result.csv  (concatenated, duplicates reported)
  - predictions/<strategy>.jsonl  (concatenated per strategy, duplicates reported)

Does NOT merge: artifacts/, logs/, patches/, or derived files
(experiment.yaml, generation_report.md, etc.) — those are half-specific.

Usage:
    python tools/merge_experiments.py EXP_A EXP_B OUTPUT_DIR
    python tools/merge_experiments.py EXP_A EXP_B OUTPUT_DIR --dry-run

Both EXP_A and EXP_B may be absolute paths or paths relative to the
current working directory (or to --base-dir if given).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path


def _resolve_exp_dir(spec: str, base_dir: Path) -> Path:
    p = Path(spec)
    if p.is_absolute():
        return p
    candidate = base_dir / p
    if candidate.exists():
        return candidate
    return p


def _merge_csv(a: Path, b: Path, out: Path, dry_run: bool) -> dict[str, int]:
    """Merge two generation_result.csv files.

    Returns row counts per strategy and reports duplicate instance_ids.
    """
    rows_a: list[dict] = []
    rows_b: list[dict] = []

    if a.exists():
        with a.open(newline="", encoding="utf-8") as f:
            rows_a = list(csv.DictReader(f))
    if b.exists():
        with b.open(newline="", encoding="utf-8") as f:
            rows_b = list(csv.DictReader(f))

    all_rows = rows_a + rows_b
    if not all_rows:
        print("  [csv] No rows found in either CSV.")
        return {}

    fieldnames = list(all_rows[0].keys())

    id_key = "instance_id"
    strategy_key = "strategy"
    combo_keys = [(r.get(id_key, ""), r.get(strategy_key, "")) for r in all_rows]
    duplicates = [k for k, cnt in Counter(combo_keys).items() if cnt > 1]
    if duplicates:
        print(f"  [csv] WARNING: {len(duplicates)} duplicate (instance_id, strategy) pairs:")
        for d in duplicates[:10]:
            print(f"        {d}")
        if len(duplicates) > 10:
            print(f"        ... and {len(duplicates) - 10} more")

    strategy_counts: dict[str, int] = Counter(r.get(strategy_key, "") for r in all_rows)
    total = len(all_rows)
    print(f"  [csv] {len(rows_a)} rows from A + {len(rows_b)} rows from B = {total} total")
    for strat, cnt in sorted(strategy_counts.items()):
        print(f"        strategy={strat!r}: {cnt} rows")

    if dry_run:
        print(f"  [csv] DRY-RUN: would write {total} rows to {out}")
        return dict(strategy_counts)

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"  [csv] Written: {out}")
    return dict(strategy_counts)


def _merge_jsonl(a: Path, b: Path, out: Path, strategy: str, dry_run: bool) -> None:
    """Merge two per-strategy .jsonl files."""
    records: list[dict] = []
    for src in (a, b):
        if src.exists():
            with src.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))

    id_field = "instance_id"
    ids = [r.get(id_field, "") for r in records]
    dups = [iid for iid, cnt in Counter(ids).items() if cnt > 1]
    if dups:
        print(f"  [jsonl/{strategy}] WARNING: {len(dups)} duplicate instance_ids: {dups[:5]}")

    print(f"  [jsonl/{strategy}] {len(records)} records total")

    if dry_run:
        print(f"  [jsonl/{strategy}] DRY-RUN: would write to {out}")
        return

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    print(f"  [jsonl/{strategy}] Written: {out}")


def merge(exp_a: Path, exp_b: Path, output_dir: Path, dry_run: bool) -> None:
    print(f"Merging:\n  A = {exp_a}\n  B = {exp_b}\n  OUT = {output_dir}")
    if dry_run:
        print("  (DRY-RUN mode: no files written)")
    print()

    csv_a = exp_a / "generation_result.csv"
    csv_b = exp_b / "generation_result.csv"
    csv_out = output_dir / "generation_result.csv"
    _merge_csv(csv_a, csv_b, csv_out, dry_run)
    print()

    strategies_seen: set[str] = set()
    for pred_dir in (exp_a / "predictions", exp_b / "predictions"):
        if pred_dir.exists():
            for jf in pred_dir.glob("*.jsonl"):
                strategies_seen.add(jf.stem)

    for strategy in sorted(strategies_seen):
        jsonl_a = exp_a / "predictions" / f"{strategy}.jsonl"
        jsonl_b = exp_b / "predictions" / f"{strategy}.jsonl"
        jsonl_out = output_dir / "predictions" / f"{strategy}.jsonl"
        _merge_jsonl(jsonl_a, jsonl_b, jsonl_out, strategy, dry_run)

    if not dry_run:
        meta = {
            "merged_from": [str(exp_a), str(exp_b)],
            "strategies": sorted(strategies_seen),
        }
        meta_path = output_dir / "merge_meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, indent=2))
        print(f"\nMeta written: {meta_path}")
    else:
        print("\nDRY-RUN complete. Re-run without --dry-run to write files.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("exp_a", help="First experiment directory (A = django half)")
    parser.add_argument("exp_b", help="Second experiment directory (B = non-django half)")
    parser.add_argument("output_dir", help="Output directory for merged results")
    parser.add_argument(
        "--base-dir",
        default="results",
        help="Base directory for resolving relative EXP paths (default: results)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without writing any files",
    )
    args = parser.parse_args()

    base_dir = Path(args.base_dir)
    exp_a = _resolve_exp_dir(args.exp_a, base_dir)
    exp_b = _resolve_exp_dir(args.exp_b, base_dir)
    output_dir = Path(args.output_dir)

    for p, label in [(exp_a, "exp_a"), (exp_b, "exp_b")]:
        if not p.exists():
            print(f"ERROR: {label} directory not found: {p}", file=sys.stderr)
            sys.exit(1)

    merge(exp_a, exp_b, output_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
