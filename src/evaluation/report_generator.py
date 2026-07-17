#!/usr/bin/env python
"""
Generate full evaluation report from Modal results + experiment data.

Usage:
    python -m src.evaluation.report_generator results/EXP-20260717-009
"""

import json
from pathlib import Path
from typing import Optional

import pandas as pd


def load_experiment_data(exp_dir: Path) -> pd.DataFrame:
    """Load results.csv from experiment dir."""
    csv_path = exp_dir / "results.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"results.csv not found in {exp_dir}")
    return pd.read_csv(csv_path)


def load_eval_results(exp_dir: Path) -> dict:
    """Load all *_results.json files from predictions/."""
    predictions_dir = exp_dir / "predictions"
    if not predictions_dir.exists():
        return {}

    eval_results = {}
    for json_file in predictions_dir.glob("*_results.json"):
        with open(json_file, "r") as f:
            data = json.load(f)
        strategy = json_file.stem.replace("_results", "").replace("predictions", "")
        eval_results[strategy] = data
    return eval_results


def merge_data(exp_df: pd.DataFrame, eval_results: dict) -> pd.DataFrame:
    """Merge evaluation results (resolved) into experiment DataFrame."""
    df = exp_df.copy()

    # Create resolved column from eval results
    resolved_map = {}
    for strategy, data in eval_results.items():
        for result in data.get("results", []):
            if isinstance(result, dict):
                instance_id = result.get("instance_id")
                resolved = result.get("resolved", False)
                resolved_map[(instance_id, strategy)] = resolved

    # Add resolved column
    def get_resolved(row):
        key = (row["instance_id"], row["strategy"])
        return resolved_map.get(key, False)

    df["resolved"] = df.apply(get_resolved, axis=1)
    return df


def generate_reports(df: pd.DataFrame, exp_dir: Path) -> Path:
    """Generate all report files in exp_dir/eval/."""
    eval_dir = exp_dir / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)

    # 1. results.csv — raw per instance
    raw_cols = [
        "instance_id", "strategy", "difficulty", "resolved",
        "inference_count", "execution_time",
        "prompt_tokens", "completion_tokens", "total_tokens",
        "cost_usd", "cost_idr", "patch_preview",
    ]
    raw_cols = [c for c in raw_cols if c in df.columns]
    results_df = df[raw_cols].copy()
    results_df.to_csv(eval_dir / "results.csv", index=False)

    # 2. repository_summary.csv — per repo × strategy
    if "repo" in df.columns:
        repo_summary = df.groupby(["repo", "strategy"]).agg(
            total=("resolved", "count"),
            resolved=("resolved", "sum"),
            success_rate=("resolved", "mean"),
            avg_time=("execution_time", "mean"),
            avg_tokens=("total_tokens", "mean"),
            avg_cost=("cost_usd", "mean"),
        ).reset_index()
        repo_summary["success_rate"] = (repo_summary["success_rate"] * 100).round(1)
        repo_summary.to_csv(eval_dir / "repository_summary.csv", index=False)

    # 3. strategy_summary.csv — global per strategy
    strategy_summary = df.groupby("strategy").agg(
        total=("resolved", "count"),
        resolved=("resolved", "sum"),
        success_rate=("resolved", "mean"),
        avg_time=("execution_time", "mean"),
        avg_tokens=("total_tokens", "mean"),
        avg_cost=("cost_usd", "mean"),
    ).reset_index()
    strategy_summary["success_rate"] = (strategy_summary["success_rate"] * 100).round(1)
    strategy_summary.to_csv(eval_dir / "strategy_summary.csv", index=False)

    # 4. tradeoff_summary.csv — cost per successful fix
    tradeoff = df.groupby("strategy").agg(
        effectiveness_pct=("resolved", "mean"),
        avg_time_s=("execution_time", "mean"),
        avg_tokens=("total_tokens", "mean"),
        total_cost_usd=("cost_usd", "sum"),
        resolved_count=("resolved", "sum"),
    ).reset_index()
    tradeoff["effectiveness_pct"] = (tradeoff["effectiveness_pct"] * 100).round(1)
    tradeoff["cost_per_success_usd"] = (
        tradeoff["total_cost_usd"] / tradeoff["resolved_count"].replace(0, pd.NA)
    ).round(6)
    tradeoff["cost_per_success_idr"] = (
        tradeoff["cost_per_success_usd"] * 17914.0
    ).round(2)
    tradeoff.to_csv(eval_dir / "tradeoff_summary.csv", index=False)

    # 5. summary.md — narrative report
    _generate_summary_md(df, eval_dir / "summary.md")

    # 6. statistics.json — structured data
    stats = {
        "strategy_summary": strategy_summary.to_dict(orient="index"),
        "tradeoff_summary": tradeoff.to_dict(orient="index"),
        "total_instances": len(df),
        "total_resolved": int(df["resolved"].sum()),
        "overall_success_rate": round(df["resolved"].mean() * 100, 1),
    }
    if "repo" in df.columns:
        stats["repository_summary"] = repo_summary.to_dict(orient="index")
    with open(eval_dir / "statistics.json", "w") as f:
        json.dump(stats, f, indent=2, default=str)

    # 7. figures/ — charts
    try:
        _generate_figures(df, eval_dir / "figures")
    except ImportError:
        print("Warning: matplotlib not installed, skipping figures")

    return eval_dir


def _generate_summary_md(df: pd.DataFrame, out_path: Path) -> None:
    """Generate narrative summary markdown."""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Evaluation Summary\n\n")

        # RQ1 — Effectiveness
        f.write("## RQ1 — Effectiveness (Success Rate)\n\n")
        strat_success = df.groupby("strategy")["resolved"].mean() * 100
        for strategy, rate in strat_success.items():
            f.write(f"- **{strategy}**: {rate:.1f}% resolved\n")
        f.write("\n")

        # RQ2 — Efficiency
        f.write("## RQ2 — Efficiency (Time & Tokens)\n\n")
        strat_time = df.groupby("strategy")["execution_time"].mean()
        strat_tokens = df.groupby("strategy")["total_tokens"].mean()
        for strategy in strat_time.index:
            f.write(
                f"- **{strategy}**: {strat_time[strategy]:.1f}s avg, "
                f"{strat_tokens[strategy]:,.0f} avg tokens\n"
            )
        f.write("\n")

        # RQ3 — Cost Tradeoff
        f.write("## RQ3 — Cost Tradeoff\n\n")
        strat_cost = df.groupby("strategy")["cost_usd"].sum()
        strat_resolved = df.groupby("strategy")["resolved"].sum()
        for strategy in strat_cost.index:
            resolved = strat_resolved[strategy]
            cost_per_fix = (strat_cost[strategy] / resolved) if resolved > 0 else float("nan")
            f.write(
                f"- **{strategy}**: ${strat_cost[strategy]:.4f} total, "
                f"${cost_per_fix:.4f} per successful fix\n"
            )
        f.write("\n")


def _generate_figures(df: pd.DataFrame, fig_dir: Path) -> None:
    """Generate charts using matplotlib."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig_dir.mkdir(parents=True, exist_ok=True)

    # Success rate by strategy
    plt.figure(figsize=(8, 5))
    strat_success = df.groupby("strategy")["resolved"].mean() * 100
    strat_success.plot(kind="bar", color="skyblue")
    plt.title("Success Rate by Strategy")
    plt.ylabel("Resolved (%)")
    plt.ylim(0, 100)
    plt.tight_layout()
    plt.savefig(fig_dir / "success_rate_by_strategy.png")
    plt.close()

    # Tokens by strategy
    plt.figure(figsize=(8, 5))
    strat_tokens = df.groupby("strategy")["total_tokens"].mean()
    strat_tokens.plot(kind="bar", color="lightgreen")
    plt.title("Average Tokens by Strategy")
    plt.ylabel("Tokens")
    plt.tight_layout()
    plt.savefig(fig_dir / "tokens_by_strategy.png")
    plt.close()

    # Cost by strategy
    plt.figure(figsize=(8, 5))
    strat_cost = df.groupby("strategy")["cost_usd"].sum()
    strat_cost.plot(kind="bar", color="salmon")
    plt.title("Total Cost by Strategy (USD)")
    plt.ylabel("Cost (USD)")
    plt.tight_layout()
    plt.savefig(fig_dir / "cost_by_strategy.png")
    plt.close()

    # Cost vs Success scatter
    plt.figure(figsize=(8, 5))
    for strategy in df["strategy"].unique():
        sub = df[df["strategy"] == strategy]
        plt.scatter(
            sub["cost_usd"],
            sub["resolved"].astype(int),
            label=strategy,
            alpha=0.7,
        )
    plt.title("Cost vs Success by Strategy")
    plt.xlabel("Cost (USD)")
    plt.ylabel("Resolved (0/1)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(fig_dir / "cost_vs_success.png")
    plt.close()

    # Difficulty analysis
    if "difficulty" in df.columns:
        plt.figure(figsize=(8, 5))
        diff_success = df.groupby(["difficulty", "strategy"])["resolved"].mean().unstack()
        diff_success.plot(kind="bar")
        plt.title("Success Rate by Difficulty")
        plt.ylabel("Resolved (%)")
        plt.ylim(0, 1)
        plt.tight_layout()
        plt.savefig(fig_dir / "difficulty_analysis.png")
        plt.close()


def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m src.evaluation.report_generator <exp_dir>")
        sys.exit(1)

    exp_dir = Path(sys.argv[1])
    if not exp_dir.exists():
        print(f"Error: {exp_dir} not found")
        sys.exit(1)

    print(f"Loading experiment data from {exp_dir}...")
    exp_df = load_experiment_data(exp_dir)

    print("Loading evaluation results...")
    eval_results = load_eval_results(exp_dir)

    print(f"Merging data ({len(exp_df)} rows)...")
    merged_df = merge_data(exp_df, eval_results)

    print(f"Generating reports in {exp_dir}/eval/...")
    eval_dir = generate_reports(merged_df, exp_dir)

    print(f"\nReports generated:")
    for file in sorted(eval_dir.rglob("*")):
        if file.is_file():
            print(f"  - {file.relative_to(eval_dir)}")


if __name__ == "__main__":
    main()
