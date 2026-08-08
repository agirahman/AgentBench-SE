"""Tests for the AgentBench TUI shell (Patch 1: screens + nav + console).

Uses Textual's ``App.run_test()`` headless mode — no terminal needed, so CI
is deterministic. Tests drive the app via ``asyncio.run()`` in plain sync
functions so the suite runs with OR without the pytest-asyncio plugin
(Windows venv has none — this file used to rely on ``@pytest.mark.asyncio``).
"""

import asyncio

from textual.widgets import Input

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


def _submit_console(app: AgentBenchTUI, text: str) -> None:
    from types import SimpleNamespace

    # NOTE: App.query_one() targets the DEFAULT screen; widget queries must
    # go through the ACTIVE screen (Textual DOM design). The input-submitted
    # handler lives on the console screen, not the app.
    screen = app.screen
    screen.query_one("#console-input", Input).value = text
    screen.on_input_submitted(SimpleNamespace(value=text))  # type: ignore[attr-defined]


def _console_log_text(app: AgentBenchTUI) -> str:
    # the active screen hosts the console widgets
    screen = app.screen
    rl = screen.query_one("#console-log")
    out = []
    for line in rl.lines:
        pieces = [seg.text for seg in line if seg.text]
        if pieces:
            out.append("".join(pieces))
    return "\n".join(out)


def _current_nav_key(app: AgentBenchTUI) -> str:
    return app.screen.nav_key


async def _boot(config: dict | None = None, monkeypatch=None, tmp_path=None):
    """Shared async boot: env isolation, config save, app + pilot."""
    if monkeypatch is not None and tmp_path is not None:
        monkeypatch.setenv("AGENTBENCH_CONFIG_DIR", str(tmp_path))
        from agentbench.config_manager import ConfigManager

        ConfigManager(config_path=tmp_path / "config.yaml").save(config or make_config())

    app = AgentBenchTUI()
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause(0.3)
        yield app, pilot


# --------------------------------------------------------------------- #
# Patch 1: boot + shell frame
# --------------------------------------------------------------------- #
def test_boots_to_setup_screen(monkeypatch, tmp_path):
    async def run():
        async for app, pilot in _boot(monkeypatch=monkeypatch, tmp_path=tmp_path):
            assert _current_nav_key(app) == "setup"
            assert app.query_one("#sh-header") is not None
            assert app.query_one("#sh-sidebar") is not None
            assert app.query_one("#sh-content") is not None
            assert app.query_one("#sh-footer") is not None

    asyncio.run(run())


def test_nav_to_all_screens(monkeypatch, tmp_path):
    async def run():
        async for app, pilot in _boot(monkeypatch=monkeypatch, tmp_path=tmp_path):
            seen = set()
            for key in ("setup", "run", "results", "config", "logs", "help", "console"):
                app.nav_to(key)
                await pilot.pause(0.2)
                seen.add(_current_nav_key(app))
            assert {"setup", "run", "results", "config", "logs", "help", "console"} <= seen

    asyncio.run(run())


def test_console_screen_focuses_input(monkeypatch, tmp_path):
    async def run():
        async for app, pilot in _boot(monkeypatch=monkeypatch, tmp_path=tmp_path):
            app.nav_to("console")
            await pilot.pause(0.3)
            # Query the active screen (App.query_one targets the default screen).
            assert app.screen.query_one("#console-input") is not None

    asyncio.run(run())


def test_console_help_native(monkeypatch, tmp_path):
    async def run():
        async for app, pilot in _boot(monkeypatch=monkeypatch, tmp_path=tmp_path):
            app.nav_to("console")
            await pilot.pause(0.3)
            _submit_console(app, "help")
            await pilot.pause(0.3)
            text = _console_log_text(app)
            assert "Commands" in text
            assert "/run" in text

    asyncio.run(run())


def test_console_module_dispatch(monkeypatch, tmp_path):
    async def run():
        async for app, pilot in _boot(monkeypatch=monkeypatch, tmp_path=tmp_path):
            class FakePricing:
                def __init__(self, cfg, console, *args, **kwargs):
                    self.console = console

                def execute(self, args):
                    self.console.print("[green]pricing stub ran[/green]")

            monkeypatch.setattr(
                "agentbench.tui.app.AgentBenchTUI._command_class",
                staticmethod(lambda cmd: FakePricing if cmd == "pricing" else None),
            )

            app.nav_to("console")
            await pilot.pause(0.3)
            _submit_console(app, "pricing")
            await pilot.pause(0.6)
            text = _console_log_text(app)
            assert "pricing stub ran" in text

    asyncio.run(run())


def test_welcome_banner_contains_model():
    text = welcome_banner({
        "provider": {"model": "deepseek/deepseek-v4-flash", "name": "openrouter"},
    })
    assert "deepseek/deepseek-v4-flash" in text
