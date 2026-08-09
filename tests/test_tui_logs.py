"""Tests for Patch 7 — Logs screen + LogViewer (filter, search, yank, export).

Portable across platforms: ``asyncio.run`` + the ``_boot`` async generator
(no ``@pytest.mark.asyncio``).
"""

import asyncio
from pathlib import Path
from typing import cast

import pytest
from textual.widgets import Button, Input, Static

from agentbench.tui import clipboard
from agentbench.tui.screens.logs_screen import LogsScreen
from agentbench.tui.state import LogEntry
from agentbench.tui.widgets.log_viewer import LogViewer


def _btn(screen, id_: str) -> Button:
    return cast(Button, screen.query_one(id_))


def _entries():
    return [
        LogEntry(timestamp="2026-08-09T10:00:01", level="INFO", message="Experiment initialized"),
        LogEntry(timestamp="2026-08-09T10:00:02", level="WARN", message="TS001: retry 1/3"),
        LogEntry(
            timestamp="2026-08-09T10:00:03",
            level="ERROR",
            message="TS002: Timeout after 30s\n  at agent.run()\n  at main()",
        ),
        LogEntry(timestamp="2026-08-09T10:00:04", level="DEBUG", message="cache hit for TS001"),
        LogEntry(timestamp="2026-08-09T10:00:05", level="INFO", message="TS003 completed"),
    ]


def _timestamps():
    return [
        "2026-08-09T10:00:01",
        "2026-08-09T10:00:02",
        "2026-08-09T10:00:03",
        "2026-08-09T10:00:04",
        "2026-08-09T10:00:05",
    ]


async def _boot(**kwargs):
    from agentbench.tui.app import AgentBenchTUI

    app = AgentBenchTUI(**kwargs)
    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.pause(0.2)
        yield app, pilot


async def _logs_screen(app, pilot) -> LogsScreen:
    app.nav_to("logs")
    await pilot.pause(0.1)
    screen = app.screen
    assert isinstance(screen, LogsScreen)
    return screen


# --------------------------------------------------------------------------- #
class TestLogViewerFilters:
    def test_level_filter_minimum_severity(self):
        viewer = LogViewer()
        viewer.set_entries(_entries())
        assert viewer.visible_count == 5  # ALL

        viewer.set_level("WARN")
        # WARN + ERROR only (DEBUG/INFO excluded).
        assert viewer.visible_count == 2
        assert "ERROR" in viewer.line_at(1)

        viewer.set_level("ERROR")
        assert viewer.visible_count == 1
        assert "ERROR" in viewer.line_at(0)

        viewer.set_level("ALL")
        assert viewer.visible_count == 5

    def test_search_filters_by_substring(self):
        viewer = LogViewer()
        viewer.set_entries(_entries())
        viewer.set_search("TS001")
        assert viewer.visible_count == 2  # WARN retry + DEBUG cache hit

        viewer.set_search("timeout")
        assert viewer.visible_count == 1

        viewer.set_search("")
        assert viewer.visible_count == 5

    def test_level_and_search_combine(self):
        viewer = LogViewer()
        viewer.set_entries(_entries())
        viewer.set_level("WARN")
        viewer.set_search("TS00")
        assert viewer.visible_count == 2  # WARN + ERROR rows both match TS00

        viewer.set_search("cache")
        assert viewer.visible_count == 0  # DEBUG excluded by level


class TestLogViewerExpansion:
    def test_multiline_collapses_with_hint(self):
        viewer = LogViewer()
        viewer.set_entries(_entries())
        line = viewer.line_at(2)  # the ERROR multi-line entry
        assert "[+2 lines]" in line
        assert "at agent.run" not in line

    def test_expand_shows_full_trace(self):
        viewer = LogViewer()
        viewer.set_entries(_entries())
        viewer._cursor = 2
        viewer.action_toggle_expand()
        line = viewer.line_at(2)
        assert "at agent.run" in line
        assert "[+2 lines]" not in line

        # Collapse again.
        viewer.action_toggle_expand()
        assert "[+2 lines]" in viewer.line_at(2)


class TestLogViewerYank:
    def test_yank_single_row_without_visual_mode(self):
        viewer = LogViewer()
        viewer.set_entries(_entries())
        viewer._cursor = 1
        text = viewer.yank_selection()
        assert "TS001: retry 1/3" in text
        assert "Experiment initialized" not in text

    def test_yank_visual_range(self):
        viewer = LogViewer()
        viewer.set_entries(_entries())
        viewer._cursor = 1
        viewer.action_visual_mode()  # anchor at 1
        viewer.action_cursor_down()  # extend to 2
        text = viewer.yank_selection()
        lines = text.splitlines()
        assert len(lines) == 2
        assert "TS001" in lines[0]
        assert "TS002" in lines[1]

    def test_yank_after_filter_respects_visible_rows(self):
        viewer = LogViewer()
        viewer.set_entries(_entries())
        viewer.set_level("ERROR")
        viewer._cursor = 0
        text = viewer.yank_selection()
        assert "TS002: Timeout" in text

    def test_cursor_stays_in_bounds(self):
        viewer = LogViewer()
        viewer.set_entries(_entries())
        viewer._cursor = 4
        viewer.action_cursor_down()  # no-op at bottom
        assert viewer._cursor == 4
        viewer.action_cursor_up()
        assert viewer._cursor == 3


class TestLogViewerExport:
    def test_export_lines_visible_only(self):
        viewer = LogViewer()
        viewer.set_entries(_entries())
        viewer.set_level("WARN")
        text = viewer.export_lines()
        assert "WARN" in text and "ERROR" in text
        assert "DEBUG" not in text
        assert "Experiment initialized" not in text


# --------------------------------------------------------------------------- #
class TestLogsScreen:
    def test_mounts_and_replays_state_logs(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                app.state.log("INFO", "boot message")
                app.state.log("WARN", "warning message")
                screen = await _logs_screen(app, pilot)
                viewer = screen._viewer
                assert viewer.total_count == 2
                assert "boot message" in viewer.export_lines()

        asyncio.run(run())

    def test_live_append_via_state_log(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                screen = await _logs_screen(app, pilot)
                before = screen._viewer.total_count
                app.state.log("INFO", "live line")
                await pilot.pause(0.1)
                assert screen._viewer.total_count == before + 1
                assert "live line" in screen._viewer.export_lines()

        asyncio.run(run())

    def test_level_button_filters_view(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                app.state.log("INFO", "info line")
                app.state.log("ERROR", "error line")
                screen = await _logs_screen(app, pilot)
                # Press the ERROR level button.
                _btn(screen, "#logs-level-error").press()
                await pilot.pause(0.05)
                assert screen._viewer.visible_count == 1
                assert "error line" in screen._viewer.export_lines()

        asyncio.run(run())

    def test_search_input_filters(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                app.state.log("INFO", "alpha line")
                app.state.log("INFO", "beta line")
                screen = await _logs_screen(app, pilot)
                inp = screen.query_one("#logs-search", Input)
                inp.value = "beta"
                await pilot.pause(0.05)
                assert screen._viewer.visible_count == 1
                assert "beta line" in screen._viewer.export_lines()

                inp.value = ""
                await pilot.pause(0.05)
                assert screen._viewer.visible_count == 2

        asyncio.run(run())

    def test_auto_scroll_toggle(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                screen = await _logs_screen(app, pilot)
                assert screen._viewer.auto_scroll is True
                screen.action_toggle_scroll()
                await pilot.pause(0.05)
                assert screen._viewer.auto_scroll is False
                status = str(cast(Static, screen.query_one("#logs-status")).content)
                assert "auto-scroll OFF" in status

        asyncio.run(run())

    def test_copy_visible_writes_tempfile_fallback(self, tmp_path, monkeypatch):
        async def run():
            async for app, pilot in _boot():
                app.state.log("INFO", "copyable line")
                screen = await _logs_screen(app, pilot)
                # Force the temp-file fallback so the test is hermetic.
                monkeypatch.setattr(clipboard, "_backends", lambda: [])
                monkeypatch.setattr(
                    clipboard, "TEMP_CLIPBOARD", tmp_path / "cb.txt"
                )
                screen.action_copy_visible()
                await pilot.pause(0.05)
                assert "copyable line" in (tmp_path / "cb.txt").read_text(
                    encoding="utf-8"
                )

        asyncio.run(run())

    def test_export_writes_file(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                app.state.config.setdefault("experiment", {})[
                    "output_dir"
                ] = str(tmp_path)
                app.state.log("INFO", "exported line")
                screen = await _logs_screen(app, pilot)
                screen.action_export_logs()
                await pilot.pause(0.05)
                files = list((tmp_path / "export").glob("logs-*.txt"))
                assert len(files) == 1
                assert "exported line" in files[0].read_text(encoding="utf-8")

        asyncio.run(run())

    def test_context_menu_save_to_file(self, tmp_path, monkeypatch):
        async def run():
            async for app, pilot in _boot():
                app.state.log("INFO", "menu line")
                screen = await _logs_screen(app, pilot)
                monkeypatch.chdir(tmp_path)
                screen.action_context_menu()
                await pilot.pause(0.1)
                from agentbench.tui.widgets.context_menu import ContextMenu

                assert isinstance(app.screen, ContextMenu)
                # Press "Save to file".
                _btn(app.screen, "#ctx-save").press()
                await pilot.pause(0.1)
                saved = tmp_path / "agentbench_clipboard_save.txt"
                assert saved.exists()
                assert "menu line" in saved.read_text(encoding="utf-8")
                # Back to the logs screen.
                assert isinstance(app.screen, LogsScreen)

        asyncio.run(run())

    def test_clear_logs(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                app.state.log("INFO", "to be cleared")
                screen = await _logs_screen(app, pilot)
                _btn(screen, "#logs-clear").press()
                await pilot.pause(0.05)
                assert screen._viewer.total_count == 0
                assert app.state.logs.items == []

        asyncio.run(run())
