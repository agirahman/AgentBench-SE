import argparse
import sys

import pandas as pd

from evaluation.statistics import (
    compute_avg_time_per_inference,
    compute_cost_per_success,
    compute_success_rate,
    compute_summary,
)


DEFAULT_CSV = "results/csv/experiment_results.csv"


def load_data(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["error"] = df["error"].fillna("")
    return df


def build_strategy_difficulty_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate execution metrics by strategy and difficulty."""
    if df.empty:
        return pd.DataFrame(columns=["execution_time", "total_tokens", "cost_usd", "success_rate"])

    grouped = (
        df.groupby(["strategy", "difficulty"])
        .agg(
            execution_time=("execution_time", "mean"),
            total_tokens=("total_tokens", "sum"),
            cost_usd=("cost_usd", "sum"),
            success_rate=("success", "mean"),
        )
        .reset_index()
    )
    return grouped.set_index(["strategy", "difficulty"])


def cmd_list(df: pd.DataFrame):
    print(df.to_string(index=False))


def cmd_summary(df: pd.DataFrame):
    summary = compute_summary(df)
    print("=== Summary (Mean/Median/Std) ===")
    print(summary.to_string())
    
    sr = compute_success_rate(df)
    print("\n=== Success Rate ===")
    print(sr.to_string())
    
    ati = compute_avg_time_per_inference(df)
    print("\n=== Avg Time per Inference ===")
    print(ati.to_string())


def cmd_compare(df: pd.DataFrame):
    pivot = df.pivot_table(
        index="instance_id",
        columns="strategy",
        values=["execution_time", "total_tokens", "inference_count"],
        aggfunc="first",
    )
    print(pivot.to_string())


def cmd_errors(df: pd.DataFrame):
    errors = df[df["error"].str.len() > 0]
    if errors.empty:
        print("No errors found.")
        return
    print(errors[["instance_id", "strategy", "error"]].to_string(index=False))


def cmd_patch(df: pd.DataFrame, patch_id: str, strategy: str | None):
    rows = df[df["instance_id"] == patch_id]
    if strategy:
        rows = rows[rows["strategy"] == strategy]
    if rows.empty:
        print(f"No patch found for {patch_id}")
        return
    for _, row in rows.iterrows():
        print(f"--- {row['instance_id']} / {row['strategy']} ---")
        print(row["patch_preview"])
        print()


def cmd_cost_per_success(df: pd.DataFrame):
    cps = compute_cost_per_success(df)
    print("=== Cost per Successful Fix ===")
    print(cps.to_string())


def cmd_strategy_difficulty(df: pd.DataFrame):
    summary = build_strategy_difficulty_summary(df)
    print("=== Strategy × Difficulty Summary ===")
    print(summary.to_string())


def main():
    raw = sys.argv[1:]
    csv_path = DEFAULT_CSV
    rest = []

    i = 0
    while i < len(raw):
        if raw[i] == "--file" and i + 1 < len(raw):
            csv_path = raw[i + 1]
            i += 2
        else:
            rest.append(raw[i])
            i += 1

    parser = argparse.ArgumentParser(description="View experiment results")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Show all results")

    sub.add_parser("summary", help="Show statistics per strategy")

    sub.add_parser("compare", help="Compare strategies side by side")

    sub.add_parser("errors", help="List experiments with errors")

    sub.add_parser("cost_per_success", help="Show cost per successful fix per strategy")

    sub.add_parser(
        "strategy_difficulty",
        help="Show aggregated metrics by strategy and difficulty",
    )

    patch_p = sub.add_parser("patch", help="Show patch preview for an issue")
    patch_p.add_argument("patch_id", help="Instance ID (e.g. django__django-10914)")
    patch_p.add_argument("--strategy", default=None, help="Filter by strategy")

    args = parser.parse_args(rest)
    df = load_data(csv_path)

    if args.command == "list":
        cmd_list(df)
    elif args.command == "summary":
        cmd_summary(df)
    elif args.command == "compare":
        cmd_compare(df)
    elif args.command == "errors":
        cmd_errors(df)
    elif args.command == "patch":
        cmd_patch(df, args.patch_id, args.strategy)
    elif args.command == "cost_per_success":
        cmd_cost_per_success(df)
    elif args.command == "strategy_difficulty":
        cmd_strategy_difficulty(df)


if __name__ == "__main__":
    main()
