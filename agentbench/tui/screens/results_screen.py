"""Results screen: sortable/filterable results table + drill-down (Patch 6).

Loads the latest experiment ``results.csv`` (reusing
:func:`agentbench.commands.results.load_results` / ``find_latest_results_csv``)
and renders it as a sortable table: click a header to toggle sort, ``/`` to
filter, ``Enter`` on a row opens a detail modal (metrics + error + patch
preview), ``C``/``J`` export the current view to CSV/JSON, ``R`` reloads.

The screen owns a small pandas view pipeline (filter → sort → render) so
sorting is deterministic and testable instead of depending on DataTable's
internal cell-comparison semantics.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pandas as pd
from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static

from agentbench.commands.results import DEFAULT_CSV, find_latest_results_csv
from agentbench.tui.widgets.shell import ShellScreen

# Column keys exposed as sortable (DataTable key == pandas column name).
SORTABLE = (
    "instance_id",
    "strategy",
    "difficulty",
    "success",
    "execution_time",
    "total_tokens",
    "cost_usd",
)

# (header label, column key)
COLUMNS = (
    ("Instance", "instance_id"),
    ("Strategy", "strategy"),
    ("Diff", "difficulty"),
    ("OK", "success"),
    ("Time", "execution_time"),
    ("Tokens", "total_tokens"),
    ("Cost", "cost_usd"),
    ("Error", "error"),
)

TRUNCATE_ERROR = 48
MAX_ROWS = 2000


def _num(value, default: float = 0.0) -> float:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_time(seconds) -> str:
    return f"{_num(seconds):.1f}s"


def _fmt_cost(usd) -> str:
    return f"${_num(usd):.4f}"


def _fmt_tokens(n) -> str:
    return f"{int(_num(n)):,}"


def _col_str(df: pd.DataFrame, name: str) -> pd.Series:
    """Return a column as lower-case str Series (empty when missing)."""
    if name not in df.columns:
        return pd.Series([""] * len(df), index=df.index, dtype=str)
    col = cast(pd.Series, df[name])
    return col.fillna("").astype(str)


def _record_from_row(row: pd.Series) -> dict:
    """Normalise one CSV row into a dict the table + modal can consume."""
    raw_success = row.get("success")
    if raw_success is None or (isinstance(raw_success, float) and pd.isna(raw_success)):
        success = 1 if str(row.get("patch_status", "")).upper() == "VALID" else 0
    else:
        success = int(bool(raw_success))
    error = str(row.get("error") or "")
    return {
        "instance_id": str(row.get("instance_id", "")),
        "strategy": str(row.get("strategy", "")),
        "difficulty": str(row.get("difficulty") or ""),
        "model": str(row.get("model") or ""),
        "success": success,
        "execution_time": _fmt_time(row.get("execution_time")),
        "total_tokens": _fmt_tokens(row.get("total_tokens")),
        "cost_usd": _fmt_cost(row.get("cost_usd")),
        "error": error,
        "patch_preview": str(row.get("patch_preview") or ""),
    }


# --------------------------------------------------------------------------- #
class ResultsDetailModal(ModalScreen[None]):
    """Full detail for one result row (metrics + error + patch preview)."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("enter", "dismiss", "Close"),
    ]

    def __init__(self, record: dict) -> None:
        super().__init__()
        self._record = record

    def compose(self) -> ComposeResult:
        r = self._record
        mark = "✓" if r["success"] else "✗"
        yield Static(
            f"[bold accent]{r['instance_id']}[/bold accent] "
            f"/ [bold]{r['strategy']}[/bold] {mark}",
            id="rd-title",
        )
        yield Static(
            f"[dim]difficulty[/dim] {r['difficulty'] or '—'}    "
            f"[dim]model[/dim] {r['model'] or '—'}    "
            f"[dim]time[/dim] {r['execution_time']}    "
            f"[dim]tokens[/dim] {r['total_tokens']}    "
            f"[dim]cost[/dim] {r['cost_usd']}",
            id="rd-metrics",
        )
        with VerticalScroll(id="rd-body"):
            if r["error"]:
                yield Static(
                    f"[bold err]Error:[/bold err] {r['error']}", id="rd-error"
                )
            preview = r["patch_preview"]
            if preview and preview.strip():
                yield Static(
                    Syntax(preview, "diff", word_wrap=True, line_numbers=True),
                    id="rd-patch",
                )
            else:
                yield Static("[dim](no patch preview)[/dim]", id="rd-empty")
        yield Static("Esc / q / Enter — close", id="rd-hint")


# --------------------------------------------------------------------------- #
class ResultsScreen(ShellScreen):
    """Results table: sort headers, filter, drill-down modal, export."""

    nav_key = "results"
    footer_hint = (
        "Results — click header sort · / filter · Enter detail · "
        "C CSV · J JSON · R reload"
    )

    BINDINGS = [
        Binding("/", "focus_filter", "Filter"),
        Binding("e", "toggle_errors", "Errors only"),
        Binding("c", "export_csv", "CSV"),
        Binding("j", "export_json", "JSON"),
        Binding("k", "copy_csv", "Copy CSV"),
        Binding("r", "reload", "Reload"),
    ]

    def __init__(self, csv_path: str | None = None) -> None:
        super().__init__()
        # Test seam: when set, load exactly this CSV instead of auto-detecting.
        self.csv_path: str | None = csv_path
        self._records: dict[str, dict] = {}
        self._view: pd.DataFrame = pd.DataFrame()
        self._sort_col: str = "instance_id"
        self._sort_reverse: bool = False
        self._errors_only: bool = False
        self._source_label: str = "no data"

    # ------------------------------------------------------------------ #
    def body(self):
        yield Static("", id="results-status")
        with Horizontal(id="results-toolbar"):
            yield Input(
                placeholder="Filter instance / strategy / error…",
                id="results-filter",
            )
            yield Button("Errors only", id="results-errors-toggle")
            yield Button("CSV", id="results-export-csv")
            yield Button("JSON", id="results-export-json")
            yield Button("Reload", id="results-reload")
        yield DataTable(
            id="results-table",
            cursor_type="row",
            zebra_stripes=True,
        )

    def on_mount(self) -> None:
        super().on_mount()
        table = self.query_one("#results-table", DataTable)
        for label, key in COLUMNS:
            table.add_column(label, key=key)
        self.action_reload()

    # ------------------------------------------------------------------ #
    def _resolve_csv(self) -> str:
        if self.csv_path:
            return self.csv_path
        base = str(
            self.app.state.config.get("experiment", {}).get("output_dir", "results")  # type: ignore[attr-defined]
        )
        return find_latest_results_csv(base) or find_latest_results_csv() or DEFAULT_CSV

    def action_reload(self) -> None:
        """(Re)load the CSV, rebuild the filtered view and the table."""
        from agentbench.commands.results import load_results

        path = self._resolve_csv()
        try:
            df = load_results(path)
            self._source_label = path
        except FileNotFoundError:
            df = pd.DataFrame()
            self._source_label = f"{path} (not found)"
        except Exception as exc:  # noqa: BLE001 - show, don't crash
            df = pd.DataFrame()
            self._source_label = f"load error: {type(exc).__name__}: {exc}"
        self._view = df
        self._records = {}
        self._refresh()

    # ------------------------------------------------------------------ #
    def _current_df(self) -> pd.DataFrame:
        """Filtered + sorted view of the loaded data (deterministic)."""
        df = self._view
        if df.empty:
            return df
        q = self.query_one("#results-filter", Input).value.strip().lower()
        if q:
            hay = (
                _col_str(df, "instance_id")
                + " "
                + _col_str(df, "strategy")
                + " "
                + _col_str(df, "error")
            ).str.lower()
            df = df.loc[cast(pd.Series, hay.str.contains(q, na=False))]
        if self._errors_only:
            df = df.loc[cast(pd.Series, _col_str(df, "error").str.len() > 0)]
        if self._sort_col in df.columns:
            df = df.sort_values(
                self._sort_col, ascending=not self._sort_reverse, kind="mergesort"
            )
        return df.head(MAX_ROWS)

    def _refresh(self) -> None:
        df = self._current_df()
        table = self.query_one("#results-table", DataTable)
        table.clear()
        self._records = {}
        for i, (_, row) in enumerate(df.iterrows()):
            rec = _record_from_row(row)
            row_key = table.add_row(
                rec["instance_id"],
                rec["strategy"],
                rec["difficulty"],
                "✓" if rec["success"] else "✗",
                rec["execution_time"],
                rec["total_tokens"],
                rec["cost_usd"],
                rec["error"][:TRUNCATE_ERROR] or "—",
                key=f"r{i}",
            )
            self._records[str(row_key.value)] = rec

        total = len(self._view)
        shown = len(df)
        ok = 0
        if not self._view.empty and "success" in self._view.columns:
            ok = int(
                cast(
                    pd.Series,
                    pd.to_numeric(self._view["success"], errors="coerce"),
                )
                .fillna(0)
                .astype(int)
                .sum()
            )
        sort_mark = "↓" if self._sort_reverse else "↑"
        status = self.query_one("#results-status", Static)
        if total == 0:
            status.update(
                f"[bold accent]Results[/bold accent]  [dim]{self._source_label}[/dim]\n"
                "[dim]No results yet — run an experiment first, or set a CSV "
                "path (R to reload).[/dim]"
            )
        else:
            status.update(
                f"[bold accent]Results[/bold accent]  [dim]{self._source_label}[/dim]\n"
                f"[dim]{shown}/{total} rows · {ok} success · "
                f"sort {self._sort_col} {sort_mark} · "
                f"{'errors only' if self._errors_only else 'all'}[/dim]"
            )
        table.refresh()

    # ------------------------------------------------------------------ #
    # Events
    # ------------------------------------------------------------------ #
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "results-filter":
            self._refresh()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "results-errors-toggle":
            self.action_toggle_errors()
        elif btn_id == "results-export-csv":
            self.action_export_csv()
        elif btn_id == "results-export-json":
            self.action_export_json()
        elif btn_id == "results-reload":
            self.action_reload()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        key = str(event.column_key.value)
        if key not in SORTABLE:
            return
        if self._sort_col == key:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = key
            self._sort_reverse = False
        self._refresh()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        rec = self._records.get(str(event.row_key.value))
        if rec is not None:
            self.app.push_screen(ResultsDetailModal(rec))

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def action_focus_filter(self) -> None:
        self.query_one("#results-filter", Input).focus()

    def action_toggle_errors(self) -> None:
        self._errors_only = not self._errors_only
        self._refresh()

    def _export(self, fmt: str) -> None:
        df = self._current_df()
        if df.empty:
            self.notify("No rows to export.", severity="warning")
            return
        from datetime import datetime

        out_dir = (
            Path(
                str(
                    self.app.state.config.get("experiment", {}).get(  # type: ignore[attr-defined]
                        "output_dir", "results"
                    )
                )
            )
            / "export"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        if fmt == "csv":
            path = out_dir / f"results-{stamp}.csv"
            df.to_csv(path, index=False)
        else:
            path = out_dir / f"results-{stamp}.json"
            df.to_json(path, orient="records", indent=2)
        self.notify(f"Exported {len(df)} rows → {path}")

    def action_export_csv(self) -> None:
        self._export("csv")

    def action_export_json(self) -> None:
        self._export("json")

    def action_copy_csv(self) -> None:
        """Copy the current (filtered) view as CSV text to the clipboard."""
        from agentbench.tui import clipboard

        df = self._current_df()
        if df.empty:
            self.notify("No rows to copy.", severity="warning")
            return
        buf = df.to_csv(index=False)
        method, extra = clipboard.copy_text(buf)
        if method == "tempfile":
            self.notify(
                f"Copied {len(df)} rows as CSV → clipboard unavailable, saved to {extra}",
                severity="warning",
            )
        else:
            self.notify(f"Copied {len(df)} rows as CSV ({method})")
