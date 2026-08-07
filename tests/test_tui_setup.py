"""Tests for Patch 3 — Setup Screen (form, validation, state binding)."""

import asyncio
import re

import pytest

from agentbench.config_manager import ConfigManager
from agentbench.core.experiments.experiment_config import DEFAULT_REPOS
from agentbench.tui.screens.setup_screen import (
    DEFAULT_TASKS,
    SetupScreen,
    auto_output_dir,
)


@pytest.fixture
def saved_config(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBENCH_CONFIG_DIR", str(tmp_path))
    cfg = {
        "researcher": {"name": "Agi", "institution": "UNJ", "email": "a@b.com"},
        "provider": {"name": "openrouter", "api_key": "x",
                     "model": "deepseek/deepseek-v4-flash"},
        "experiment": {"temperature": 0.2, "max_retries": 3, "rate_limit": 1.5,
                       "usd_idr_rate": 16500.0},
        "pricing": {},
    }
    ConfigManager(config_path=tmp_path / "config.yaml").save(cfg)
    return cfg


async def _boot():
    from agentbench.tui.app import AgentBenchTUI

    app = AgentBenchTUI()
    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.pause(0.2)
        yield app, pilot


def _screen(app) -> SetupScreen:
    """Typed access to the active SetupScreen (tests know the boot screen)."""
    return app.screen  # type: ignore[return-value]


def _text(widget) -> str:
    return str(getattr(widget, "content", ""))


# --------------------------------------------------------------------------- #
class TestSetupFormRendering:
    def test_form_renders(self, saved_config):
        async def run():
            async for app, pilot in _boot():
                screen = _screen(app)
                assert screen.query_one("#setup-form") is not None
                assert screen.query_one("#setup-provider") is not None
                assert screen.query_one("#setup-model") is not None
                assert screen.query_one("#setup-temp") is not None
                assert screen.query_one("#setup-start") is not None
                checkboxes = screen.query("#setup-tasks Checkbox")
                assert len(checkboxes) == len(DEFAULT_TASKS)

        asyncio.run(run())

    def test_form_prefilled_from_config(self, saved_config):
        async def run():
            async for app, pilot in _boot():
                from textual.widgets import Input, Select

                screen = _screen(app)
                assert screen.query_one("#setup-model", Input).value == (
                    "deepseek/deepseek-v4-flash"
                )
                assert screen.query_one("#setup-provider", Select).value == "openrouter"
                assert screen.query_one("#setup-temp", Input).value == "0.2"

        asyncio.run(run())

    def test_task_checkboxes_use_repo_labels(self, saved_config):
        async def run():
            async for app, pilot in _boot():
                labels = [
                    getattr(c, "label", None)
                    for c in _screen(app).query("#setup-tasks Checkbox")
                ]
                assert set(labels) == set(DEFAULT_TASKS)

        asyncio.run(run())

    def test_auto_output_dir_format(self):
        outdir = auto_output_dir()
        assert re.match(r"^\./results/run_\d{8}_\d{4}$", outdir), outdir

    def test_footer_hint_declared(self):
        assert "Start" in SetupScreen.footer_hint


# --------------------------------------------------------------------------- #
class TestSetupValidation:
    def test_invalid_temperature_reported(self, saved_config):
        async def run():
            async for app, pilot in _boot():
                from textual.widgets import Input

                screen = _screen(app)
                screen.query_one("#setup-temp", Input).value = "1.5"
                assert "temp" in screen._field_errors()

        asyncio.run(run())

    def test_valid_values_pass(self, saved_config):
        async def run():
            async for app, pilot in _boot():
                assert _screen(app)._field_errors() == {}

        asyncio.run(run())

    def test_invalid_concurrency_reported(self, saved_config):
        async def run():
            async for app, pilot in _boot():
                from textual.widgets import Input

                screen = _screen(app)
                screen.query_one("#setup-concurrency", Input).value = "99"
                assert "concurrency" in screen._field_errors()
                screen.query_one("#setup-concurrency", Input).value = "0"
                assert "concurrency" in screen._field_errors()

        asyncio.run(run())

    def test_empty_model_reported(self, saved_config):
        async def run():
            async for app, pilot in _boot():
                from textual.widgets import Input

                screen = _screen(app)
                screen.query_one("#setup-model", Input).value = ""
                assert "model" in screen._field_errors()

        asyncio.run(run())

    def test_inline_error_widget_updates(self, saved_config):
        async def run():
            async for app, pilot in _boot():
                from textual.widgets import Input, Static

                screen = _screen(app)
                screen.query_one("#setup-temp", Input).value = "1.5"
                screen._show_field_errors(screen._field_errors())
                err = screen.query_one("#setup-temp-err", Static)
                assert "must be in" in _text(err)
                assert err.has_class("visible")

        asyncio.run(run())


# --------------------------------------------------------------------------- #
class TestSetupActions:
    def test_start_persists_config_and_emits(self, saved_config, tmp_path):
        async def run():
            async for app, pilot in _boot():
                screen = _screen(app)
                screen._start()
                await pilot.pause(0.2)

                # Config persisted via state (Patch 2 binding)
                assert app.state.config["provider"]["model"] == (
                    "deepseek/deepseek-v4-flash"
                )
                assert app.state.config["researcher"]["name"] == "Agi"
                # On disk too
                reloaded = ConfigManager(config_path=tmp_path / "config.yaml").load()
                assert reloaded["provider"]["model"] == "deepseek/deepseek-v4-flash"
                # setup.submitted emitted with per-run params
                types = [ev.type for ev in app.state.events.history]
                assert "setup.submitted" in types
                submitted = next(
                    ev for ev in app.state.events.history
                    if ev.type == "setup.submitted"
                )
                assert submitted.payload["concurrency"] == 1
                assert submitted.payload["output_dir"].startswith("./results/run_")
                assert set(submitted.payload["tasks"]) == set(DEFAULT_TASKS)
                # Status shows success
                assert "tersimpan" in _text(screen.query_one("#setup-status"))

        asyncio.run(run())

    def test_start_invalid_does_not_emit(self, saved_config):
        async def run():
            async for app, pilot in _boot():
                from textual.widgets import Input

                screen = _screen(app)
                screen.query_one("#setup-temp", Input).value = "9.9"
                screen._start()
                await pilot.pause(0.1)
                types = [ev.type for ev in app.state.events.history]
                assert "setup.submitted" not in types
                assert "belum valid" in _text(screen.query_one("#setup-status"))

        asyncio.run(run())

    def test_select_all_and_none(self, saved_config):
        async def run():
            async for app, pilot in _boot():
                screen = _screen(app)
                # Checkboxes are created checked (from DEFAULT_TASKS default)
                initial = [
                    getattr(c, "value", None)
                    for c in screen.query("#setup-tasks Checkbox")
                ]
                assert initial == [True] * len(DEFAULT_TASKS)

                screen._set_all_tasks(False)
                unchecked = [
                    getattr(c, "value", True)
                    for c in screen.query("#setup-tasks Checkbox")
                ]
                assert unchecked == [False] * len(DEFAULT_TASKS)
                screen._set_all_tasks(True)
                checked = [
                    getattr(c, "value", False)
                    for c in screen.query("#setup-tasks Checkbox")
                ]
                assert checked == [True] * len(DEFAULT_TASKS)

        asyncio.run(run())

    def test_start_button_press_works(self, saved_config):
        async def run():
            async for app, pilot in _boot():
                from textual.widgets import Button

                # Button may sit below the fold in a 120x45 headless viewport;
                # Button.press() simulates the press without needing visibility.
                _screen(app).query_one("#setup-start", Button).press()
                await pilot.pause(0.2)
                types = [ev.type for ev in app.state.events.history]
                assert "setup.submitted" in types

        asyncio.run(run())

    def test_load_profile_refills_form(self, saved_config):
        async def run():
            async for app, pilot in _boot():
                from textual.widgets import Input

                screen = _screen(app)
                screen.query_one("#setup-model", Input).value = "changed/model"
                screen._load_profile()
                await pilot.pause(0.1)
                assert screen.query_one("#setup-model", Input).value == (
                    "deepseek/deepseek-v4-flash"
                )
                assert screen.query_one("#setup-temp", Input).value == "0.2"

        asyncio.run(run())

    def test_collect_returns_run_params(self, saved_config):
        async def run():
            async for app, pilot in _boot():
                collected = _screen(app)._collect()
                assert collected["run_params"]["concurrency"] == 1
                assert collected["run_params"]["tasks"]
                assert collected["config_updates"]["provider"]["name"] == "openrouter"
                assert collected["config_updates"]["experiment"]["temperature"] == 0.2

        asyncio.run(run())


# --------------------------------------------------------------------------- #
class TestSetupNumericHelpers:
    def test_auto_output_dir_deterministic_with_now(self):
        from datetime import datetime

        outdir = auto_output_dir(datetime(2026, 8, 8, 9, 30))
        assert outdir == "./results/run_20260808_0930"
