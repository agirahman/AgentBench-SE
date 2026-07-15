import sys
import argparse
import pandas as pd


DEFAULT_CSV = "results/csv/experiment_results.csv"


def load_data(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["error"] = df["error"].fillna("")
    return df


def cmd_list(df: pd.DataFrame):
    print(df.to_string(index=False))


def cmd_summary(df: pd.DataFrame):
    numeric = [
        "execution_time", "inference_count", "prompt_tokens",
        "completion_tokens", "total_tokens", "cost_usd", "cost_idr",
    ]
    summary = df.groupby("strategy")[numeric].agg(["mean", "min", "max"])
    print(summary.to_string())
    
    success = (df.assign(success=df["error"].str.len() == 0)
                 .groupby("strategy")["success"].mean())
    print("\n=== Success Rate ===")
    print(success.to_string())


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
    per = df.groupby("strategy").agg(
        total_cost_usd=("cost_usd", "sum"),
        total_cost_idr=("cost_idr", "sum"),
        count=("instance_id", "count"),
    )
    success_count = (df.assign(success=df["error"].str.len() == 0)
                       .groupby("strategy")["success"].sum())
    per["success_count"] = success_count
    per["success_rate"] = per["success_count"] / per["count"]
    safe = per["success_count"].replace(0, pd.NA)
    per["cost_usd_per_success"] = per["total_cost_usd"] / safe
    per["cost_idr_per_success"] = per["total_cost_idr"] / safe
    print("=== Cost per Successful Fix ===")
    print(per.to_string())


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


if __name__ == "__main__":
    main()
