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
    out = []
    for line in lines:
        pieces = [seg.text for seg in line if seg.text]
        out.append("".join(pieces))
    return "\n".join(out)


@pytest.mark.asyncio
async def test_app_mounts_widgets():
    app = AgentBenchTUI(config={
        "provider": {"model": "m", "name": "openrouter"},
    })
    async with app.run_test(size=(100, 30)) as pilot:
        # banner is not a sticky top-pinned widget anymore
        try:
            app.query_one("#banner")
            assert False, "sticky #banner widget should not exist"
        except Exception:
            pass  # expected: no sticky banner widget
        assert app.query_one("#log") is not None
        assert app.query_one("#command-input") is not None
        text = _log_text(app)
        # banner summary lines are plain-text (not ASCII-glyph), so check those
        assert "Model" in text
        assert "Provider" in text
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
        assert "Commands" in log_text
        assert "/run" in log_text


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


# --------------------------------------------------------------------- #
# TUI-2: streaming color, collapsible details, keyboard shortcuts
# --------------------------------------------------------------------- #
def _fake_class_factory(lines):
    """Return a fake command class that prints given lines on execute."""

    class Fake:
        def __init__(self, cfg, console, cm, interactive=True):
            self.console = console
            self._ = cfg
            self.interactive = interactive

        def execute(self, args):
            for line in lines:
                self.console.print(line)

    return Fake


@pytest.mark.asyncio
async def test_streamed_output_resolves_colors(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTBENCH_CONFIG_DIR", str(tmp_path))
    from agentbench.config_manager import ConfigManager

    cm = ConfigManager(config_path=tmp_path / "config.yaml")
    cm.save(make_config())

    fake = _fake_class_factory([
        "some progress",
        "[info] WARNING: token budget low",
        "ERROR: strategy failed",
        "SUCCESS: run completed",
    ])
    monkeypatch.setattr(
        "agentbench.tui.app.AgentBenchTUI._command_class",
        lambda self, cmd: fake if cmd == "run" else None,
    )

    app = AgentBenchTUI()
    async with app.run_test(size=(110, 40)) as pilot:
        _submit(app, "run --issues 0")
        await pilot.pause(0.8)
        text = _log_text(app)
        assert "some progress" in text
        assert "WARNING" in text
        assert "ERROR" in text
        assert "SUCCESS" in text
        assert "run finished" in text


@pytest.mark.asyncio
async def test_detail_collapsible_toggles(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTBENCH_CONFIG_DIR", str(tmp_path))
    from agentbench.config_manager import ConfigManager
    from textual.widgets import Collapsible

    cm = ConfigManager(config_path=tmp_path / "config.yaml")
    cm.save(make_config())

    app = AgentBenchTUI()
    async with app.run_test(size=(110, 40)) as pilot:
        cp = app.query_one("#detail-panel", Collapsible)
        assert cp.collapsed is True
        app.action_toggle_details()
        await pilot.pause(0.2)
        assert cp.collapsed is False
        app.action_toggle_details()
        await pilot.pause(0.2)
        assert cp.collapsed is True


@pytest.mark.asyncio
async def test_keyboard_shortcut_actions(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTBENCH_CONFIG_DIR", str(tmp_path))
    from agentbench.config_manager import ConfigManager

    cm = ConfigManager(config_path=tmp_path / "config.yaml")
    cm.save(make_config())

    app = AgentBenchTUI()
    async with app.run_test(size=(110, 40)) as pilot:
        # ctrl+h -> help lands in log
        app.action_cmd_help()
        await pilot.pause(0.4)
        text = _log_text(app)
        assert "Commands" in text
        # ctrl+l clears
        app.query_one("#log").write("junk-line-to-clear")
        await pilot.pause(0.1)
        app.action_clear_log()
        await pilot.pause(0.2)
        assert "junk-line-to-clear" not in _log_text(app)


@pytest.mark.asyncio
async def test_copy_log_copies_to_clipboard(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTBENCH_CONFIG_DIR", str(tmp_path))
    from agentbench.config_manager import ConfigManager

    cm = ConfigManager(config_path=tmp_path / "config.yaml")
    cm.save(make_config())

    captured = {}

    app = AgentBenchTUI()
    async with app.run_test(size=(110, 40)) as pilot:
        # seed the log with an error line, then copy
        app.query_one("#log").write("some progress")
        app.query_one("#log").write("ERROR: something broke")
        await pilot.pause(0.1)
        monkeypatch.setattr(app, "copy_to_clipboard", lambda t: captured.__setitem__("txt", t))
        app.action_copy_log()
        await pilot.pause(0.2)
        assert "ERROR: something broke" in captured.get("txt", "")


@pytest.mark.asyncio
async def test_save_log_writes_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTBENCH_CONFIG_DIR", str(tmp_path))
    from agentbench.config_manager import ConfigManager

    cm = ConfigManager(config_path=tmp_path / "config.yaml")
    cm.save(make_config())

    app = AgentBenchTUI()
    # save_log writes to <cwd>/logs; give it a dedicated dir so the test
    # doesn't pollute the repo. Point it via the app's working directory.
    app._log_dir = tmp_path  # used by action_save_log
    async with app.run_test(size=(110, 40)) as pilot:
        app.query_one("#log").write("debug line alpha")
        app.query_one("#log").write("WARNING: something")
        await pilot.pause(0.1)
        app.action_save_log()
        await pilot.pause(0.2)
    files = list(tmp_path.glob("tui-log-*.txt"))
    assert files, "save_log should write a file"
    content = files[0].read_text(encoding="utf-8")
    assert "WARNING: something" in content