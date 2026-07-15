import json

import pandas as pd


def compute_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Mean/median/std per strategy for all numeric metrics.

    Returns a DataFrame indexed by strategy with flattened columns
    formatted as ``{stat}_{col}``.
    """
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    summary = df.groupby("strategy")[numeric].agg(["mean", "median", "std"])
    summary.columns = [f"{stat}_{col}" for col, stat in summary.columns]
    return summary


def compute_success_rate(df: pd.DataFrame) -> pd.Series:
    """Fraction of successful runs per strategy (error column empty)."""
    return (
        df.assign(success=df["error"].fillna("").str.len() == 0)
        .groupby("strategy")["success"]
        .mean()
    )


def compute_avg_time_per_inference(df: pd.DataFrame) -> pd.Series:
    """Average execution time per inference (RQ2 metric)."""
    avg_time = df.groupby("strategy")["execution_time"].mean()
    avg_infs = df.groupby("strategy")["inference_count"].mean()
    return avg_time / avg_infs


def compute_cost_per_success(df: pd.DataFrame) -> pd.DataFrame:
    """Total cost divided by success count per strategy."""
    total_cost_usd = df.groupby("strategy")["cost_usd"].sum()
    total_cost_idr = df.groupby("strategy")["cost_idr"].sum()
    success_cnt = (
        df.assign(success=df["error"].fillna("").str.len() == 0)
        .groupby("strategy")["success"]
        .sum()
    )
    safe = success_cnt.replace(0, pd.NA)
    return pd.DataFrame(
        {
            "total_cost_usd": total_cost_usd,
            "total_cost_idr": total_cost_idr,
            "success_count": success_cnt,
            "cost_usd_per_success": total_cost_usd / safe,
            "cost_idr_per_success": total_cost_idr / safe,
        }
    )


def export_statistics_json(df: pd.DataFrame, out_path: str) -> None:
    """Serialize computed metrics to a statistics.json file."""
    data = {
        "summary": compute_summary(df).to_dict(orient="index"),
        "success_rate": compute_success_rate(df).to_dict(),
        "avg_time_per_inference": compute_avg_time_per_inference(df).to_dict(),
        "cost_per_success": compute_cost_per_success(df).to_dict(orient="index"),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def generate_summary_md(df: pd.DataFrame, out_path: str) -> None:
    """Write a Markdown RQ1/RQ2/RQ3 summary to *out_path*."""
    sr = compute_success_rate(df)
    ati = compute_avg_time_per_inference(df)
    cps = compute_cost_per_success(df)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Experiment Summary\n\n")
        f.write("## RQ1 — Success Rate\n\n")
        f.write(sr.to_frame(name="success_rate").to_string())
        f.write("\n\n## RQ2 — Avg Time per Inference\n\n")
        f.write(ati.to_frame(name="avg_time_per_inference").to_string())
        f.write("\n\n## RQ3 — Cost per Successful Fix\n\n")
        f.write(cps.to_string())
        f.write("\n")
