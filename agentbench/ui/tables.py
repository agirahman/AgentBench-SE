"""Rich tables for result rendering."""

from __future__ import annotations

import pandas as pd
from rich.table import Table


def create_summary_table(rows: list[dict]) -> Table:
    """Strategy-level summary: issues, avg time, tokens, cost, success."""
    table = Table(title="Results Summary", box=None)
    table.add_column("Strategy", style="cyan")
    table.add_column("Issues", justify="right")
    table.add_column("Avg Time", justify="right")
    table.add_column("Avg Tokens", justify="right")
    table.add_column("Avg Cost", justify="right")
    table.add_column("Success", justify="right")
    for r in rows:
        table.add_row(
            r.get("strategy", ""),
            str(r.get("issues", "")),
            f"{r.get('avg_time', 0.0):.1f}s",
            f"{r.get('avg_tokens', 0):,.0f}",
            f"${r.get('avg_cost', 0.0):.4f}",
            f"{r.get('success_rate', 0.0):.1f}%",
        )
    return table


def create_compare_table(df: pd.DataFrame) -> Table:
    """Side-by-side per-issue comparison across strategies."""
    table = Table(title="Strategy Comparison", box=None)
    table.add_column("Issue", style="cyan")
    table.add_column("Strategy")
    table.add_column("Time (s)", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Cost (USD)", justify="right")
    table.add_column("Status")
    for _, row in df.iterrows():
        table.add_row(
            str(row.get("instance_id", "")),
            str(row.get("strategy", "")),
            f"{row.get('execution_time', 0.0):.1f}",
            f"{row.get('total_tokens', 0):,}",
            f"{row.get('cost_usd', 0.0):.4f}",
            str(row.get("patch_status", row.get("error", "")) or "-"),
        )
    return table


def create_errors_table(df: pd.DataFrame) -> Table:
    """Rows where error is non-empty."""
    table = Table(title="Failed Issues", box=None)
    table.add_column("Issue", style="cyan")
    table.add_column("Strategy")
    table.add_column("Error")
    for _, row in df.iterrows():
        table.add_row(
            str(row.get("instance_id", "")),
            str(row.get("strategy", "")),
            str(row.get("error", ""))[:160],
        )
    return table


def create_difficulty_table(df: pd.DataFrame) -> Table:
    """Strategy x difficulty aggregates."""
    table = Table(title="Strategy × Difficulty", box=None)
    table.add_column("Strategy", style="cyan")
    table.add_column("Difficulty")
    table.add_column("Issues", justify="right")
    table.add_column("Avg Time", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Cost (USD)", justify="right")
    for _, row in df.iterrows():
        table.add_row(
            str(row.get("strategy", "")),
            str(row.get("difficulty", "")),
            str(row.get("issues", "")),
            f"{row.get('execution_time', 0.0):.1f}s",
            f"{row.get('total_tokens', 0):,}",
            f"${row.get('cost_usd', 0.0):.4f}",
        )
    return table