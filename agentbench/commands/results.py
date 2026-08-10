"""``results`` command: view & analyze experiment results.

Subcommands: ``summary``, ``compare``, ``errors``, ``patch <id> [--strategy]``,
plus ``cost_per_success`` and ``strategy_difficulty`` re-exported from the
existing core statistics module (wrap, not rewrite).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from rich.syntax import Syntax

from agentbench.commands.base import BaseCommand

DEFAULT_CSV = "results/csv/experiment_results.csv"


def find_latest_results_csv(base_dir: str = "results") -> str | None:
    """Return the newest ``results/EXP-*/results.csv`` (by mtime), else None.

    Ties on identical mtimes (common on Windows NTFS where two quick writes
    share a timestamp) are broken by descending directory name, so the
    highest ``EXP-*`` number wins deterministically on every platform.
    """
    root = Path(base_dir)
    if not root.exists():
        return None
    candidates = sorted(
        root.glob("EXP-*/results.csv"),
        key=lambda p: (p.stat().st_mtime, p.parent.name),
        reverse=True,
    )
    return str(candidates[0]) if candidates else None


def load_results(csv_path: str | None = None) -> pd.DataFrame:
    """Load a results CSV, auto-detecting the latest experiment if omitted."""
    path = csv_path or find_latest_results_csv() or DEFAULT_CSV
    df = pd.read_csv(path)
    if "error" in df.columns:
        df["error"] = df["error"].fillna("")
    if "success" not in df.columns and "patch_status" in df.columns:
        df["success"] = df["patch_status"].apply(lambda s: 1 if s == "VALID" else 0)
    return df


class ResultsCommand(BaseCommand):
    """Handle ``results <summary|compare|errors|patch|cost_per_success|strategy_difficulty>``."""

    def __init__(self, config: dict, console, csv_path: str | None = None):
        super().__init__(config, console)
        self._csv_path = csv_path

    def execute(self, args: str) -> None:
        parts = args.split()
        sub = parts[0] if parts else "summary"

        try:
            df = load_results(self._csv_path)
        except FileNotFoundError:
            self.error(
                "No results found. Run an experiment first ('run'), or pass "
                "--file <path> to a results.csv."
            )
            return

        handlers = {
            "summary": self._summary,
            "compare": self._compare,
            "errors": self._errors,
            "patch": self._patch,
            "cost_per_success": self._cost_per_success,
            "strategy_difficulty": self._strategy_difficulty,
            "list": self._list,
        }
        handler = handlers.get(sub)
        if handler is None:
            self.error(
                f"Unknown subcommand: '{sub}'. "
                "Use: summary|compare|errors|patch|cost_per_success|strategy_difficulty"
            )
            return
        handler(df, parts[1:])

    # ------------------------------------------------------------------ #
    def _summary(self, df: pd.DataFrame, rest: list[str]) -> None:
        from agentbench.core.evaluation.statistics import (
            compute_summary,
            compute_success_rate,
        )
        from agentbench.ui.tables import create_summary_table

        summary = compute_summary(df)
        sr = compute_success_rate(df)
        counts = df.groupby("strategy").size() if "strategy" in df.columns else None

        rows = []
        for strategy, srow in summary.iterrows():
            rate = sr.get(strategy, 0.0)
            rows.append({
                "strategy": strategy,
                "issues": int(counts.get(strategy, 0)) if counts is not None else 0,
                "avg_time": float(srow.get("mean_execution_time", 0.0)),
                "avg_tokens": float(srow.get("mean_total_tokens", 0.0)),
                "avg_cost": float(srow.get("mean_cost_usd", 0.0)),
                "success_rate": float(rate) * 100,
            })
        if not rows:
            self.warning("No data to summarize.")
            return
        self.console.print(create_summary_table(rows))

    def _compare(self, df: pd.DataFrame, rest: list[str]) -> None:
        from agentbench.ui.tables import create_compare_table

        # Latest row per (issue, strategy) for a clean side-by-side
        key_cols = [c for c in ("instance_id", "strategy") if c in df.columns]
        if len(key_cols) < 2:
            self.error("Cannot compare: missing instance_id/strategy columns.")
            return
        view = df.sort_values("execution_time").drop_duplicates(
            subset=key_cols, keep="last"
        ).head(50)
        if view.empty:
            self.warning("No data to compare.")
            return
        self.console.print(create_compare_table(view))

    def _errors(self, df: pd.DataFrame, rest: list[str]) -> None:
        from agentbench.ui.tables import create_errors_table

        if "error" not in df.columns:
            self.warning("No error column in this results file.")
            return
        errors = df[df["error"].str.len() > 0]
        if errors.empty:
            self.success("No errors found. ✓")
            return
        self.console.print(create_errors_table(errors))

    def _patch(self, df: pd.DataFrame, rest: list[str]) -> None:
        if not rest:
            self.error("Usage: results patch <issue_id> [--strategy S]")
            return
        patch_id = rest[0]
        strategy = None
        if "--strategy" in rest:
            i = rest.index("--strategy")
            if i + 1 < len(rest):
                strategy = rest[i + 1]

        rows = df[df["instance_id"] == patch_id]
        if strategy:
            rows = rows[rows["strategy"] == strategy]
        if rows.empty:
            self.error(f"No patch found for {patch_id}"
                       + (f" / {strategy}" if strategy else ""))
            return

        for _, row in rows.iterrows():
            preview = row.get("patch_preview", "")
            if not isinstance(preview, str) or not preview.strip():
                preview = row.get("error", "(no patch preview)")
            self.console.print(
                f"\n[bold cyan]--- {row['instance_id']} / {row['strategy']} ---[/bold cyan]"
            )
            self.console.print(Syntax(preview, "diff", word_wrap=True))

    def _cost_per_success(self, df: pd.DataFrame, rest: list[str]) -> None:
        from agentbench.core.evaluation.statistics import compute_cost_per_success

        cps = compute_cost_per_success(df)
        if cps is None or cps.empty:
            self.warning("No data for cost per success.")
            return
        self.console.print(cps.to_string())

    def _strategy_difficulty(self, df: pd.DataFrame, rest: list[str]) -> None:
        if "difficulty" not in df.columns:
            self.warning("No difficulty column in this results file.")
            return
        from agentbench.core.view_results import build_strategy_difficulty_summary
        from agentbench.ui.tables import create_difficulty_table

        agg = build_strategy_difficulty_summary(df).reset_index()
        if agg.empty:
            self.warning("No data for strategy × difficulty.")
            return
        agg["issues"] = 1  # placeholder count per row (group size)
        self.console.print(create_difficulty_table(agg))

    def _list(self, df: pd.DataFrame, rest: list[str]) -> None:
        self.console.print(df.head(50).to_string(index=False))