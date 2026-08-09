"""Tests for Patch 6 — Results Screen (sortable table, filter, drill-down, export).

Portable across platforms: uses ``asyncio.run`` + the ``_boot`` async
generator (no ``@pytest.mark.asyncio`` — the Windows venv has no plugin).
"""

import asyncio
import csv
from typing import Any, cast

import pytest
from textual.widgets import DataTable, Input, Static

from agentbench.tui.screens.results_screen import (
    ResultsDetailModal,
    ResultsScreen,
)

# Columns mirror a real EXP-*/results.csv from the backend.
CSV_HEADER = [
    "instance_id",
    "strategy",
    "model",
    "difficulty",
    "inference_count",
    "execution_time",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "patch_preview",
    "input_cost_usd",
    "output_cost_usd",
    "cost_usd",
    "cost_idr",
    "pricing_version",
    "success",
    "error",
    "patch_status",
    "timestamp",
]

ROWS = [
    {
        "instance_id": "django__django-1000",
        "strategy": "strat1_direct",
        "model": "deepseek-v4-flash",
        "difficulty": "easy",
        "execution_time": 10.5,
        "total_tokens": 1500,
        "cost_usd": 0.0012,
        "success": 1,
        "error": "",
        "patch_preview": "diff --git a/x.py b/x.py\n+pass\n",
    },
    {
        "instance_id": "django__django-1000",
        "strategy": "strat2_plan",
        "model": "deepseek-v4-flash",
        "difficulty": "hard",
        "execution_time": 30.2,
        "total_tokens": 5000,
        "cost_usd": 0.0045,
        "success": 0,
        "error": "TimeoutError: agent exceeded step limit",
        "patch_preview": "",
    },
    {
        "instance_id": "requests__requests-200",
        "strategy": "strat1_direct",
        "model": "deepseek-v4-flash",
        "difficulty": "medium",
        "execution_time": 5.1,
        "total_tokens": 800,
        "cost_usd": 0.0008,
        "success": 1,
        "error": "",
        "patch_preview": "diff --git a/y.py b/y.py\n-foo\n+bar\n",
    },
    {
        "instance_id": "requests__requests-200",
        "strategy": "strat3_loop",
        "model": "deepseek-v4-flash",
        "difficulty": "easy",
        "execution_time": 12.3,
        "total_tokens": 2100,
        "cost_usd": 0.0019,
        "success": 0,
        "error": "RateLimitError: provider quota exhausted",
        "patch_preview": "",
    },
    {
        "instance_id": "seaborn__seaborn-50",
        "strategy": "strat2_plan",
        "model": "deepseek-v4-flash",
        "difficulty": "easy",
        "execution_time": 8.7,
        "total_tokens": 1200,
        "cost_usd": 0.0011,
        "success": 1,
        "error": "",
        "patch_preview": "diff --git a/z.py b/z.py\n+import os\n",
    },
    {
        "instance_id": "seaborn__seaborn-50",
        "strategy": "strat3_loop",
        "model": "deepseek-v4-flash",
        "difficulty": "hard",
        "execution_time": 20.4,
        "total_tokens": 3400,
        "cost_usd": 0.0028,
        "success": 1,
        "error": "",
        "patch_preview": "diff --git a/w.py b/w.py\n-pass\n+def f():\n",
    },
]


def _write_csv(path, rows=None, header=None):
    rows = ROWS if rows is None else rows
    header = CSV_HEADER if header is None else header
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in header})


async def _boot(**kwargs):
    from agentbench.tui.app import AgentBenchTUI

    app = AgentBenchTUI(**kwargs)
    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.pause(0.2)
        yield app, pilot


async def _results_screen(app, pilot, csv_path) -> ResultsScreen:
    """Navigate to Results and force-load a specific CSV (test seam)."""
    app.nav_to("results")
    await pilot.pause(0.1)
    screen = app.screen
    assert isinstance(screen, ResultsScreen)
    screen.csv_path = str(csv_path)
    screen.reload()
    await pilot.pause(0.05)
    return screen


def _table(screen: ResultsScreen) -> DataTable:
    return cast(DataTable, screen.query_one("#results-table"))


def _status(screen: ResultsScreen) -> str:
    return str(cast(Static, screen.query_one("#results-status")).content)


def _filter(screen: ResultsScreen) -> Input:
    return cast(Input, screen.query_one("#results-filter"))


def _header_event(key: str) -> Any:
    class Key:
        value = key

    class HeaderEvent:
        column_key = Key()

    return HeaderEvent()


def _row_event(row_key: str) -> Any:
    class RowKey:
        value = row_key

    class RowEvent:
        row_key = RowKey()

    return RowEvent()


def _cell(table: DataTable, row: int, col: int) -> str:
    """Read a table cell, silencing the Coordinate typing noise."""
    return str(table.get_cell_at(cast(Any, (row, col))))


# --------------------------------------------------------------------------- #
class TestResultsTableRendering:
    def test_results_screen_mounts_with_table_and_rows(self, tmp_path):
        async def run():
            csv_path = tmp_path / "results.csv"
            _write_csv(csv_path)
            async for app, pilot in _boot():
                screen = await _results_screen(app, pilot, csv_path)
                assert _table(screen).row_count == 6
                status = _status(screen)
                assert "6/6 rows" in status
                assert "4 success" in status

        asyncio.run(run())

    def test_results_empty_state_when_csv_missing(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                missing = tmp_path / "nope.csv"
                screen = await _results_screen(app, pilot, missing)
                assert _table(screen).row_count == 0
                assert "No results yet" in _status(screen)

        asyncio.run(run())

    def test_results_error_rows_shown_with_truncated_error(self, tmp_path):
        async def run():
            csv_path = tmp_path / "results.csv"
            _write_csv(csv_path)
            async for app, pilot in _boot():
                screen = await _results_screen(app, pilot, csv_path)
                assert "TimeoutError" in _cell(_table(screen), 1, 7)
                assert len(_cell(_table(screen), 1, 7)) <= 48

        asyncio.run(run())


# --------------------------------------------------------------------------- #
class TestResultsSortAndFilter:
    def test_sort_by_cost_header_changes_order(self, tmp_path):
        async def run():
            csv_path = tmp_path / "results.csv"
            _write_csv(csv_path)
            async for app, pilot in _boot():
                screen = await _results_screen(app, pilot, csv_path)
                table = _table(screen)

                # Default sort: instance_id ascending → django rows first.
                assert screen._sort_col == "instance_id"

                # Click the Cost header (key "cost_usd").
                screen.on_data_table_header_selected(_header_event("cost_usd"))
                await pilot.pause(0.02)

                # Ascending by cost: cheapest (0.0008) first.
                assert screen._sort_col == "cost_usd"
                assert screen._sort_reverse is False
                assert "requests__requests-200" in _cell(table, 0, 0)

                # Click again → descending: most expensive (0.0045) first.
                screen.on_data_table_header_selected(_header_event("cost_usd"))
                await pilot.pause(0.02)
                assert screen._sort_reverse is True
                assert "django__django-1000" in _cell(table, 0, 0)

        asyncio.run(run())

    def test_filter_input_narrows_rows(self, tmp_path):
        async def run():
            csv_path = tmp_path / "results.csv"
            _write_csv(csv_path)
            async for app, pilot in _boot():
                screen = await _results_screen(app, pilot, csv_path)
                filt = _filter(screen)

                filt.value = "seaborn"
                await pilot.pause(0.02)
                assert _table(screen).row_count == 2
                assert "2/6 rows" in _status(screen)

                filt.value = "django__django"
                await pilot.pause(0.02)
                assert _table(screen).row_count == 2

        asyncio.run(run())

    def test_errors_only_toggle(self, tmp_path):
        async def run():
            csv_path = tmp_path / "results.csv"
            _write_csv(csv_path)
            async for app, pilot in _boot():
                screen = await _results_screen(app, pilot, csv_path)
                assert screen._errors_only is False
                screen.action_toggle_errors()
                await pilot.pause(0.02)
                assert _table(screen).row_count == 2  # only the two error rows
                assert "errors only" in _status(screen)

        asyncio.run(run())


# --------------------------------------------------------------------------- #
class TestResultsDrillDown:
    def test_row_enter_opens_detail_modal(self, tmp_path):
        async def run():
            csv_path = tmp_path / "results.csv"
            _write_csv(csv_path)
            async for app, pilot in _boot():
                screen = await _results_screen(app, pilot, csv_path)
                # Simulate Enter on the first visible row.
                row_key = next(iter(screen._records))
                screen.on_data_table_row_selected(_row_event(row_key))
                await pilot.pause(0.1)
                assert isinstance(app.screen, ResultsDetailModal)
                title = str(
                    cast(Static, app.screen.query_one("#rd-title")).content
                )
                assert "django__django-1000" in title

                # Close the modal.
                app.screen.dismiss(None)
                await pilot.pause(0.1)
                assert isinstance(app.screen, ResultsScreen)

        asyncio.run(run())

    def test_modal_shows_patch_and_error(self, tmp_path):
        async def run():
            csv_path = tmp_path / "results.csv"
            _write_csv(csv_path)
            async for app, pilot in _boot():
                screen = await _results_screen(app, pilot, csv_path)
                # Find the record with an error (django/strat2_plan).
                row_key = next(k for k, v in screen._records.items() if v["error"])
                screen.on_data_table_row_selected(_row_event(row_key))
                await pilot.pause(0.1)
                modal = app.screen
                assert isinstance(modal, ResultsDetailModal)
                err = str(cast(Static, modal.query_one("#rd-error")).content)
                assert "TimeoutError" in err
                # No patch preview for the error row → "no patch preview".
                assert modal.query("#rd-patch") or modal.query("#rd-empty")

        asyncio.run(run())


# --------------------------------------------------------------------------- #
class TestResultsExport:
    def test_export_csv_and_json(self, tmp_path):
        async def run():
            csv_path = tmp_path / "results.csv"
            _write_csv(csv_path)
            async for app, pilot in _boot():
                # Point the experiment output dir at tmp_path so exports land
                # in tmp_path/export/.
                app.state.config.setdefault("experiment", {})[
                    "output_dir"
                ] = str(tmp_path)
                screen = await _results_screen(app, pilot, csv_path)

                screen.action_export_csv()
                screen.action_export_json()
                await pilot.pause(0.05)

                exports = list((tmp_path / "export").glob("results-*"))
                csvs = [p for p in exports if p.suffix == ".csv"]
                jsons = [p for p in exports if p.suffix == ".json"]
                assert len(csvs) == 1
                assert len(jsons) == 1

                # CSV re-reads to the same row count.
                import pandas as pd

                df = pd.read_csv(csvs[0])
                assert len(df) == 6
                assert "instance_id" in df.columns

                # JSON parses to 6 records.
                import json

                with open(jsons[0], encoding="utf-8") as fh:
                    assert len(json.load(fh)) == 6

        asyncio.run(run())

    def test_export_empty_view_warns(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                missing = tmp_path / "nope.csv"
                screen = await _results_screen(app, pilot, missing)
                screen.action_export_csv()
                await pilot.pause(0.05)
                assert not (tmp_path / "export").exists()

        asyncio.run(run())
