#!/usr/bin/env python
"""Aggregate multiple experiment runs into mean +- std per strategy.

Used to gauge run-to-run stability when rerunning the same configuration
(e.g. 3 runs with no seed support, as DeepSeek has none). Each run is a
separate ``results.csv`` under ``results/<group>/EXP-YYYYMMDD-NNN/``.

Usage:
    python tools/aggregate_runs.py results/dry_run_multiagent
    python tools/aggregate_runs.py results/dry_run_multiagent --csv results.csv
    python tools/aggregate_runs.py EXP1 EXP2 EXP3 --metrics resolution_rate cost_usd execution_time

For each requested metric the tool prints, per strategy:
    mean, std, min, max  across the N runs (a run = one EXP folder).

If a ``resolved`` column is present it is aggregated too; otherwise the
generation-success proxy (error empty) is used for the success metric.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd


def discover_csvs(roots: list[Path], csv_name: str = "results.csv") -> list[Path]:
    """Collect results.csv files from given roots.

    A root may be:
      * a directory containing results.csv directly, or
      * a directory whose subfolders each contain results.csv (the EXP layout).
    """
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            print(f"[warn] path not found: {root}", file=sys.stderr)
            continue
        direct = root / csv_name
        if direct.exists():
            found.append(direct)
            continue
        for csv in sorted(root.rglob(csv_name)):
            found.append(csv)
    return found


def load_runs(csvs: list[Path]) -> list[pd.DataFrame]:
    runs = []
    for csv in csvs:
        try:
            df = pd.read_csv(csv)
            df["_run"] = csv.parent.name  # EXP id for traceability
            runs.append(df)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] failed reading {csv}: {e}", file=sys.stderr)
    return runs


def _success_series(df: pd.DataFrame) -> pd.Series:
    if "resolved" in df.columns:
        return df["resolved"].astype(float)
    return (df["error"].fillna("").str.len() == 0).astype(float)


def aggregate(runs: list[pd.DataFrame], metrics: list[str]) -> pd.DataFrame:
    """Return per-strategy mean/std/min/max for each metric across runs."""
    rows = []
    for strategy in sorted({s for df in runs for s in df["strategy"].unique()}):
        for metric in metrics:
            values = []
            for df in runs:
                sub = df[df["strategy"] == strategy]
                if sub.empty:
                    continue
                if metric == "resolution_rate":
                    values.append(_success_series(sub).mean())
                elif metric == "generation_success_rate":
                    values.append((sub["error"].fillna("").str.len() == 0).mean())
                elif metric in sub.columns and pd.api.types.is_numeric_dtype(sub[metric]):
                    values.append(sub[metric].sum() if metric in ("cost_usd", "cost_idr", "execution_time", "total_tokens") else sub[metric].mean())
                else:
                    continue
            if not values:
                continue
            s = pd.Series(values)
            rows.append(
                {
                    "strategy": strategy,
                    "metric": metric,
                    "n_runs": len(s),
                    "mean": s.mean(),
                    "std": s.std(ddof=0),
                    "min": s.min(),
                    "max": s.max(),
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "roots",
        nargs="+",
        type=Path,
        help="EXP directories or a parent group directory containing them.",
    )
    parser.add_argument("--csv", default="generation_result.csv", help="CSV filename to aggregate (default: generation_result.csv)")
    parser.add_argument(
        "--metrics",
        nargs="*",
        default=["resolution_rate", "cost_usd", "execution_time", "total_tokens"],
        help="Metrics to aggregate. 'resolution_rate' uses resolved if present.",
    )
    parser.add_argument("--out", default=None, help="Optional path to write the aggregated CSV.")
    args = parser.parse_args()

    csvs = discover_csvs(args.roots, args.csv)
    if not csvs:
        print("No results.csv found in the given roots.", file=sys.stderr)
        sys.exit(1)
    print(f"Found {len(csvs)} run(s):")
    for c in csvs:
        print(f"  - {c}")

    runs = load_runs(csvs)
    if not runs:
        print("No runs loaded.", file=sys.stderr)
        sys.exit(1)

    agg = aggregate(runs, args.metrics)
    with pd.option_context("display.max_rows", None, "display.width", 160):
        print("\n=== Aggregated mean +- std per strategy ===")
        print(agg.to_string(index=False))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        agg.to_csv(args.out, index=False)
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
