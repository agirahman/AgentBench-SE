"""Tests for Patch 9 — performance & stability (SDD §7.8).

Covers: lazy pandas import at TUI boot (the ~750 ms startup win),
bounded RichLog live pane, and the lazy ``_pd()`` helper. Portable:
subprocess checks + asyncio.run, no pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from typing import cast

from textual.widgets import RichLog

from agentbench.tui.screens import results_screen

APP_IMPORT = "from agentbench.tui.app import AgentBenchTUI"

# Repo root resolved from this file, so the subprocess checks keep working
# no matter where pytest is invoked from (WSL venv, Windows venv, etc.).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _boot(**kwargs):
    """Minimal AgentBenchTUI boot (same shape as other TUI test suites)."""
    from agentbench.tui.app import AgentBenchTUI

    app = AgentBenchTUI(**kwargs)

    async def _run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause(0.2)
            yield app, pilot

    return _run()


class TestLazyPandasStartup:
    """The app module must boot without dragging pandas into sys.modules."""

    def test_import_app_does_not_import_pandas(self):
        """pandas is the heaviest dep (~750 ms); results_screen must defer it."""
        code = (
            "import sys; "
            f"{APP_IMPORT}; "
            "assert 'pandas' not in sys.modules, "
            "'pandas was imported eagerly at TUI boot'"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,  # subprocess needs the repo root importable
        )
        assert proc.returncode == 0, proc.stderr

    def test_import_app_does_not_import_commands_results(self):
        """agentbench.commands.results is the pandas gateway; keep it lazy too."""
        code = (
            "import sys; "
            f"{APP_IMPORT}; "
            "assert 'agentbench.commands.results' not in sys.modules"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
        )
        assert proc.returncode == 0, proc.stderr

    def test_results_screen_is_registered(self):
        """The screen module itself is still importable and mounted."""
        assert callable(results_screen._pd)

    def test_pd_helper_caches_module(self):
        """_pd() imports pandas on first use and caches it."""
        first = results_screen._pd()
        second = results_screen._pd()
        assert first is second
        assert first is not None


class TestRunScreenMemory:
    """The live log pane must be bounded (Patch 9, SDD §7.8)."""

    def test_run_log_has_max_lines(self):
        async def run():
            async for app, pilot in _boot():
                app.nav_to("run")
                await pilot.pause(0.2)
                log = cast(RichLog, app.screen.query_one("#run-log", RichLog))
                assert log.max_lines == 1000

        asyncio.run(run())

    def test_run_screen_replays_bounded_log(self):
        async def run():
            async for app, pilot in _boot():
                app.state.log("info", "seed line")
                app.nav_to("run")
                await pilot.pause(0.2)
                log = cast(RichLog, app.screen.query_one("#run-log", RichLog))
                assert log.max_lines == 1000
                assert len(log.lines) >= 1

        asyncio.run(run())