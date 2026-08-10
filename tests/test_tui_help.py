"""Tests for Patch 8 — Help screen, action sheet, accessibility report, polish.

Portable across platforms: ``asyncio.run`` + the ``_boot`` async generator
(no ``@pytest.mark.asyncio``).
"""

import asyncio
from typing import cast

import pytest
from textual.widgets import Button, Static

from agentbench.tui import shortcuts
from agentbench.tui.screens.help_screen import HelpScreen
from agentbench.tui.widgets.action_sheet import ActionSheet


async def _boot(**kwargs):
    from agentbench.tui.app import AgentBenchTUI

    app = AgentBenchTUI(**kwargs)
    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.pause(0.2)
        yield app, pilot


async def _help_screen(app, pilot) -> HelpScreen:
    app.nav_to("help")
    await pilot.pause(0.1)
    screen = app.screen
    assert isinstance(screen, HelpScreen)
    return screen


def _help_text(screen: HelpScreen) -> str:
    return str(cast(Static, screen.query_one("#help-content")).content)


# --------------------------------------------------------------------------- #
class TestShortcutRegistry:
    def test_registry_covers_every_nav_screen(self):
        from agentbench.tui.widgets.shell import NAV_ITEMS

        agg = shortcuts.screen_bindings()
        assert set(agg) == {key for key, _ in NAV_ITEMS}

    def test_report_flags_no_missing_handlers(self):
        report = shortcuts.binding_report()
        for key, data in report.items():
            assert data["missing"] == [], (
                f"screen '{key}' has bound actions without handlers: "
                f"{data['missing']}"
            )

    def test_visible_bindings_have_descriptions(self):
        for key, bindings in shortcuts.screen_bindings().items():
            for bkey, desc in bindings:
                assert bkey and desc, f"{key}: binding {bkey!r} missing description"

    def test_copy_paste_cheatsheet_present(self):
        keys = [k for k, _ in shortcuts.COPY_PASTE_SHORTCUTS]
        assert "K" in keys and "Alt+C" in keys and "v" in keys

    def test_global_shortcuts_include_quit_and_action_sheet(self):
        keys = [k for k, _ in shortcuts.GLOBAL_SHORTCUTS]
        assert "Ctrl+Q" in keys and "?" in keys


# --------------------------------------------------------------------------- #
class TestHelpScreen:
    def test_mounts_and_shows_sections(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                screen = await _help_screen(app, pilot)
                text = _help_text(screen)
                assert "Keyboard Cheatsheet" in text
                assert "Navigasi Global" in text
                assert "Copy & Paste" in text
                # Per-screen sections (label-based).
                assert "Setup" in text
                assert "Results" in text
                assert "Logs" in text

        asyncio.run(run())

    def test_shows_real_bindings(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                screen = await _help_screen(app, pilot)
                text = _help_text(screen)
                # Bindings from the registry must appear in the cheatsheet.
                for key, _desc in shortcuts.GLOBAL_SHORTCUTS:
                    assert key in text
                for key, _desc in shortcuts.COPY_PASTE_SHORTCUTS:
                    assert key in text
                # A screen-specific binding, e.g. Results 'k' (copy CSV).
                assert "k" in text

        asyncio.run(run())

    def test_footer_hint_mentions_shortcuts(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                screen = await _help_screen(app, pilot)
                assert "cheatsheet" in screen.footer_hint.lower()

        asyncio.run(run())


# --------------------------------------------------------------------------- #
class TestActionSheet:
    def test_question_mark_opens_action_sheet(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                await _help_screen(app, pilot)
                await pilot.press("?")
                await pilot.pause(0.1)
                assert isinstance(app.screen, ActionSheet)
                body = str(
                    cast(Static, app.screen.query_one("#as-body")).content
                )
                assert "Global" in body
                assert "Help" in body  # active screen label
                assert "Ctrl+Q" in body

        asyncio.run(run())

    def test_escape_closes_action_sheet(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                await _help_screen(app, pilot)
                await pilot.press("?")
                await pilot.pause(0.1)
                assert isinstance(app.screen, ActionSheet)
                await pilot.press("escape")
                await pilot.pause(0.1)
                assert isinstance(app.screen, HelpScreen)

        asyncio.run(run())

    def test_action_sheet_shows_active_screen_bindings(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                app.nav_to("results")
                await pilot.pause(0.1)
                await pilot.press("?")
                await pilot.pause(0.1)
                assert isinstance(app.screen, ActionSheet)
                body = str(
                    cast(Static, app.screen.query_one("#as-body")).content
                )
                assert "Results" in body
                assert "Copy CSV" in body  # the 'k' binding description

        asyncio.run(run())


# --------------------------------------------------------------------------- #
class TestPolish:
    def test_logs_empty_state_message(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                app.nav_to("logs")
                await pilot.pause(0.1)
                from agentbench.tui.screens.logs_screen import LogsScreen

                screen = cast(LogsScreen, app.screen)
                assert screen._viewer.total_count == 0
                text = str(
                    cast(Static, screen.query_one("#logs-status")).content
                )
                assert "No log entries yet" in text

        asyncio.run(run())

    def test_header_status_dot_marks_running(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                app.state._run_active = True
                app.nav_to("help")
                await pilot.pause(0.1)
                header = str(
                    cast(Static, app.screen.query_one("#sh-header")).content
                )
                assert "yellow" in header  # running dot

                app.state._run_active = False
                app.nav_to("setup")
                await pilot.pause(0.1)
                header = str(
                    cast(Static, app.screen.query_one("#sh-header")).content
                )
                assert "green" in header  # idle dot

        asyncio.run(run())

    def test_sidebar_has_focusable_nav_buttons(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                await _help_screen(app, pilot)
                from agentbench.tui.widgets.shell import NAV_ITEMS

                for key, _label in NAV_ITEMS:
                    btn = cast(Button, app.screen.query_one(f"#nav-{key}"))
                    assert btn.can_focus
                # Tab from the help screen moves focus somewhere (no crash).
                await pilot.press("tab")
                await pilot.pause(0.05)
                assert app.focused is not None

        asyncio.run(run())

    def test_results_empty_state_already_present(self, tmp_path):
        async def run():
            async for app, pilot in _boot():
                app.nav_to("results")
                await pilot.pause(0.1)
                from agentbench.tui.screens.results_screen import ResultsScreen

                screen = cast(ResultsScreen, app.screen)
                # Point at a CSV that does not exist → guaranteed empty view.
                screen.csv_path = str(tmp_path / "missing.csv")
                screen.action_reload()
                await pilot.pause(0.1)
                text = str(
                    cast(Static, screen.query_one("#results-status")).content
                )
                assert "No results yet" in text

        asyncio.run(run())
