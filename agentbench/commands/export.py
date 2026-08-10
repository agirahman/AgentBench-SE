"""``export`` command: write results to CSV / JSON / Markdown.

Wraps the loaded results DataFrame; no core rewrite.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from agentbench.commands.base import BaseCommand
from agentbench.commands.results import load_results

VALID_FORMATS = ("csv", "json", "markdown")


class ExportCommand(BaseCommand):
    """Handle ``export [--format csv|json|markdown] [--output PATH]``."""

    def __init__(self, config: dict, console, csv_path: str | None = None):
        super().__init__(config, console)
        self._csv_path = csv_path

    def execute(self, args: str) -> None:
        parsed = self.parse_args(args)
        fmt = str(parsed.get("format", "csv")).lower()
        if fmt not in VALID_FORMATS:
            self.error(
                f"Unknown format '{fmt}'. Use: {'|'.join(VALID_FORMATS)}"
            )
            return

        try:
            df = load_results(self._csv_path)
        except FileNotFoundError:
            self.error("No results found. Run an experiment first ('run').")
            return

        if df.empty:
            self.error("Results are empty — nothing to export.")
            return

        out = self._resolve_output(parsed, fmt)
        try:
            if fmt == "csv":
                path = self._export_csv(df, out)
            elif fmt == "json":
                path = self._export_json(df, out)
            else:
                path = self._export_markdown(df, out)
        except OSError as e:
            self.error(f"Export failed: {e}")
            return

        self.success(f"Exported to {path}")

    # ------------------------------------------------------------------ #
    def _resolve_output(self, parsed: dict, fmt: str) -> Path:
        raw = parsed.get("output")
        if raw and raw is not True:
            return Path(str(raw))
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return Path(f"results/export-{stamp}.{fmt}")

    def _export_csv(self, df, out: Path) -> str:
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False)
        return str(out)

    def _export_json(self, df, out: Path) -> str:
        out.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "row_count": int(len(df)),
            "columns": list(df.columns),
            "strategies": sorted(df["strategy"].unique().tolist())
            if "strategy" in df.columns else [],
            "issues": df.to_dict(orient="records"),
        }
        out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(out)

    def _export_markdown(self, df, out: Path) -> str:
        out.parent.mkdir(parents=True, exist_ok=True)
        cols = [c for c in (
            "instance_id", "strategy", "difficulty", "execution_time",
            "total_tokens", "cost_usd", "patch_status", "error",
        ) if c in df.columns]

        lines = ["# AgentBench-SE Experiment Results", ""]
        lines.append(f"_Exported {datetime.now(timezone.utc).isoformat()} — {len(df)} rows_")
        lines.append("")
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "|".join(["---"] * len(cols)) + "|")
        for _, row in df.iterrows():
            cells = []
            for c in cols:
                v = row.get(c, "")
                s = "" if v is None else str(v)
                s = s.replace("|", "\\|").replace("\n", " ")[:80]
                cells.append(s)
            lines.append("| " + " | ".join(cells) + " |")
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(out)