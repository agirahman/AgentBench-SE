"""Tests for the Textual TUI (TUI-1): mount, banner, native + module dispatch.

Uses Textual's ``App.run_test()`` headless mode — no real terminal needed,
which keeps CI deterministic.
"""

import pytest

from agentbench.tui.app import AgentBenchTUI
from agentbench.tui.banner import welcome_banner


def make_config() -> dict:
    return {
        "researcher": {"name": "Agi", "institution": "UNJ", "email": "a@b.com"},
        "provider": {"name": "openrouter", "api_key": "x",
                     "model": "deepseek/deepseek-v4-flash"},
        "experiment": {"temperature": 0.2, "max_retries": 3, "rate_limit": 1.5,
                       "usd_idr_rate": 16500.0},
        "pricing": {},
    }


def _submit(app: AgentBenchTUI, text: str) -> None:
    """Simulate typing a command into the input and pressing Enter."""
    from types import SimpleNamespace

    app.query_one("#command-input").value = text
    app.on_input_submitted(SimpleNamespace(value=text))


def _log_text(app: AgentBenchTUI) -> str:
    """Extract the RichLog contents as plain text."""
    lines = app.query_one("#log").lines
    return "\n".join(str(line) for line in lines)


@pytest.mark.asyncio
async def test_app_mounts_widgets():
    app = AgentBenchTUI(config={
        "provider": {"model": "m", "name": "openrouter"},
    })
    async with app.run_test(size=(100, 30)) as pilot:
        assert app.query_one("#banner") is not None
        assert app.query_one("#log") is not None
        assert app.query_one("#command-input") is not None
        await pilot.pause()


@pytest.mark.asyncio
async def test_help_native_command():
    app = AgentBenchTUI(config={
        "provider": {"model": "m", "name": "openrouter"},
        "researcher": {"name": "Agi"},
    })
    async with app.run_test(size=(100, 30)) as pilot:
        _submit(app, "help")
        await pilot.pause(0.3)
        log_text = _log_text(app)
        assert "Available commands" in log_text


@pytest.mark.asyncio
async def test_info_native_command_shows_model():
    app = AgentBenchTUI(config={
        "provider": {"model": "deepseek/deepseek-v4-flash", "name": "openrouter"},
        "researcher": {"name": "Agi", "institution": "UNJ"},
    })
    async with app.run_test(size=(100, 30)) as pilot:
        _submit(app, "info")
        await pilot.pause(0.3)
        log_text = _log_text(app)
        assert "deepseek/deepseek-v4-flash" in log_text


@pytest.mark.asyncio
async def test_module_command_routes(monkeypatch, tmp_path):
    """Module commands route to real classes; capture into the log."""
    monkeypatch.setenv("AGENTBENCH_CONFIG_DIR", str(tmp_path))
    from agentbench.config_manager import ConfigManager

    cm = ConfigManager(config_path=tmp_path / "config.yaml")
    cm.save(make_config())

    # stub the command class so we don't hit OpenRouter/network
    class FakePricing:
        def __init__(self, cfg, console, cm):
            self.console = console
            self._ = cfg

        def execute(self, args):
            self.console.print("[green]pricing stub ran[/green]")

    monkeypatch.setattr(
        "agentbench.tui.app.AgentBenchTUI._command_class",
        lambda self, cmd: FakePricing if cmd == "pricing" else None,
    )

    app = AgentBenchTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        _submit(app, "pricing show")
        await pilot.pause(0.6)
        log_text = _log_text(app)
        assert "pricing stub ran" in log_text


def test_welcome_banner_contains_model():
    text = welcome_banner({
        "provider": {"model": "deepseek/deepseek-v4-flash", "name": "openrouter"},
    })
    assert "deepseek/deepseek-v4-flash" in text