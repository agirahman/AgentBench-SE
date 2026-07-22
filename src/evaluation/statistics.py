import json
from collections import Counter

import pandas as pd


def _ensure_strategy_column(df: pd.DataFrame) -> pd.DataFrame:
    if "strategy" not in df.columns:
        df = df.copy()
        df["strategy"] = "unknown"
    return df


def _ensure_error_column(df: pd.DataFrame) -> pd.DataFrame:
    if "error" not in df.columns:
        df = df.copy()
        df["error"] = ""
    return df


def _ensure_patch_status_column(df: pd.DataFrame) -> pd.DataFrame:
    if "patch_status" not in df.columns:
        df = df.copy()
        df["patch_status"] = "UNKNOWN"
    return df


def compute_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Mean/median/std per strategy for all numeric metrics.

    Returns a DataFrame indexed by strategy with flattened columns
    formatted as ``{stat}_{col}``.
    """
    df = _ensure_strategy_column(df)
    if df.empty:
        return pd.DataFrame()
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    summary = df.groupby("strategy")[numeric].agg(["mean", "median", "std"])
    if summary.empty:
        return pd.DataFrame()
    summary.columns = [f"{stat}_{col}" for col, stat in summary.columns]
    return summary


def compute_success_rate(df: pd.DataFrame) -> pd.Series:
    """Fraction of successful runs per strategy (error column empty)."""
    df = _ensure_strategy_column(_ensure_error_column(df))
    if df.empty:
        return pd.Series(dtype="float64")
    return (
        df.assign(success=df["error"].fillna("").str.len() == 0)
        .groupby("strategy")["success"]
        .mean()
    )


def compute_avg_time_per_inference(df: pd.DataFrame) -> pd.Series:
    """Average execution time per inference (RQ2 metric)."""
    df = _ensure_strategy_column(df)
    if df.empty:
        return pd.Series(dtype="float64")
    avg_time = df.groupby("strategy")["execution_time"].mean()
    avg_infs = df.groupby("strategy")["inference_count"].mean()
    return avg_time / avg_infs.replace(0, pd.NA)


def compute_cost_per_success(df: pd.DataFrame) -> pd.DataFrame:
    """Total cost divided by success count per strategy."""
    df = _ensure_strategy_column(_ensure_error_column(df))
    if df.empty:
        return pd.DataFrame()
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


def compute_patch_validity_rate(df: pd.DataFrame) -> pd.Series:
    """Fraction of valid patches per strategy (VALID + NORMALIZE)."""
    df = _ensure_strategy_column(_ensure_patch_status_column(df))
    if df.empty:
        return pd.Series(dtype="float64")
    return (
        df.assign(valid=df["patch_status"].isin(["VALID", "NORMALIZE"]))
        .groupby("strategy")["valid"]
        .mean()
    )


def compute_patch_quality(df: pd.DataFrame) -> pd.DataFrame:
    """Patch Quality breakdown per strategy: VALID / NORMALIZE / INVALID."""
    df = _ensure_strategy_column(_ensure_patch_status_column(df))
    if df.empty:
        return pd.DataFrame()
    total = df.groupby("strategy")["patch_status"].size()
    valid = df[df["patch_status"] == "VALID"].groupby("strategy").size()
    norm = df[df["patch_status"] == "NORMALIZE"].groupby("strategy").size()
    inv = df[~df["patch_status"].isin(["VALID", "NORMALIZE"])].groupby("strategy").size()

    result = pd.DataFrame({
        "total": total,
        "valid": valid,
        "normalize": norm,
        "invalid": inv,
        "valid_pct": valid / total * 100,
        "normalize_pct": norm / total * 100,
        "invalid_pct": inv / total * 100,
    }).fillna(0)
    return result


def summarize_run_failure(df: pd.DataFrame) -> pd.DataFrame:
    """Count per patch_status per strategy."""
    df = _ensure_strategy_column(_ensure_patch_status_column(df))
    if df.empty:
        return pd.DataFrame()
    return (
        df.groupby(["strategy", "patch_status"])
        .size()
        .unstack(fill_value=0)
    )


def export_statistics_json(df: pd.DataFrame, out_path: str, pricing: dict | None = None, usd_idr_rate: float = 16500.0) -> None:
    """Serialize computed metrics to a statistics.json file."""
    df = _ensure_strategy_column(df)
    model_name = df["model"].iloc[0] if "model" in df.columns and len(df) else "unknown"
    data = {
        "summary": compute_summary(df).to_dict(orient="index"),
        "success_rate": compute_success_rate(df).to_dict(),
        "avg_time_per_inference": compute_avg_time_per_inference(df).to_dict(),
        "cost_per_success": compute_cost_per_success(df).to_dict(orient="index"),
        "patch_validity_rate": compute_patch_validity_rate(df).to_dict(),
        "patch_quality": compute_patch_quality(df).to_dict(),
        "failure_breakdown": summarize_run_failure(df).to_dict(),
        "pricing": {
            "model": model_name,
            "input_per_1m": (pricing or {}).get("input_per_million", 0),
            "output_per_1m": (pricing or {}).get("output_per_million", 0),
            "usd_idr_rate": usd_idr_rate,
        },
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def generate_summary_md(df: pd.DataFrame, out_path: str, pricing: dict | None = None, usd_idr_rate: float = 16500.0) -> None:
    """Write a Markdown RQ1/RQ2/RQ3 summary to *out_path*."""
    df = _ensure_strategy_column(df)
    sr = compute_success_rate(df)
    ati = compute_avg_time_per_inference(df)
    cps = compute_cost_per_success(df)
    pvr = compute_patch_validity_rate(df)
    pq = compute_patch_quality(df)
    fb = summarize_run_failure(df)
    model_name = df["model"].iloc[0] if "model" in df.columns and len(df) else "unknown"
    inp_rate = (pricing or {}).get("input_per_million", 0)
    out_rate = (pricing or {}).get("output_per_million", 0)
    pricing_ver = (pricing or {}).get("pricing_version", "?")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Experiment Summary\n\n")
        f.write("**Pricing Configuration**\n\n")
        f.write(f"- Model: {model_name}\n")
        f.write(f"- Pricing Source: {pricing_ver}\n")
        f.write(f"- Input: ${inp_rate:.2f}/1M tokens · Output: ${out_rate:.2f}/1M tokens\n")
        f.write(f"- Exchange Rate: 1 USD = Rp{usd_idr_rate:,.0f}\n")
        f.write(f"- Cost Formula: Input Tokens + Output Tokens\n\n")

        f.write("## RQ1 — Success Rate\n\n")
        f.write(sr.to_frame(name="success_rate").to_string())

        f.write("\n\n## RQ2 — Avg Time per Inference\n\n")
        f.write(ati.to_frame(name="avg_time_per_inference").to_string())

        f.write("\n\n## RQ3 — Cost per Successful Fix\n\n")
        cps_out = cps.copy()
        if not cps_out.empty:
            if "cost_usd_per_success" in cps_out.columns:
                cps_out["cost_usd_per_success"] = cps_out["cost_usd_per_success"].apply(lambda x: f"${x:.6f}")
            if "cost_idr_per_success" in cps_out.columns:
                cps_out["cost_idr_per_success"] = cps_out["cost_idr_per_success"].apply(lambda x: f"Rp{x:,.0f}")
            total_usd = cps_out["total_cost_usd"].sum()
            total_idr = cps_out["total_cost_idr"].sum()
        else:
            total_usd = 0.0
            total_idr = 0.0
        f.write(cps_out.to_string())
        f.write(f"\n\n**Total Cost: ${total_usd:.6f} (Rp{total_idr:,.0f})**\n")

        f.write("\n\n## Patch Validity Rate\n\n")
        f.write(pvr.to_frame(name="patch_validity_rate").to_string())

        f.write("\n\n## Patch Quality\n\n")
        f.write(pq.to_string())

        f.write("\n\n## Failure Breakdown (per patch_status)\n\n")
        f.write(fb.to_string())
        f.write("\n")
