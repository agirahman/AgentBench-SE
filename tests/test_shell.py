"""Tests for the interactive shell (AgentBenchShell)."""

import pytest
from rich.console import Console
from rich.panel import Panel

from agentbench.shell import AgentBenchShell

VALID_CONFIG = {
    "researcher": {"name": "Agi", "institution": "UNJ", "email": "a@b.com"},
    "provider": {"name": "openrouter", "api_key": "sk-super-secret-key",
                 "model": "tencent/hy3:free"},
    "experiment": {"temperature": 0.2, "max_retries": 3, "rate_limit": 1.5,
                   "usd_idr_rate": 16500.0},
}


@pytest.fixture
def shell(tmp_path):
    """A shell wired to a temp config file with valid content."""
    from agentbench.config_manager import ConfigManager

    cm = ConfigManager(config_path=tmp_path / "config.yaml")
    cm.save(VALID_CONFIG)
    console = Console(force_terminal=True, width=100, record=True)
    s = AgentBenchShell(config=cm.load(), console=console)
    s.config_manager = cm
    return s


def test_prompt_is_set():
    assert "agentbench>" in AgentBenchShell.prompt


def test_preloop_prints_banner(shell):
    shell.preloop()
    text = shell.console.export_text()
    assert "AgentBench-SE Interactive Shell" in text
    assert "Agi" in text
    assert "tencent/hy3:free" in text


def test_do_help_lists_categories(shell):
    shell.do_help("")
    text = shell.console.export_text()
    assert "Available Commands" in text
    assert "Experiment" in text
    assert "Analysis" in text


def test_do_version(shell):
    shell.do_version("")
    assert "0.1.0" in shell.console.export_text()


def test_do_exit_returns_true(shell):
    assert shell.do_exit("") is True
    assert "Goodbye" in shell.console.export_text()


def test_do_quit_returns_true(shell):
    assert shell.do_quit("") is True


def test_do_EOF_returns_true(shell):
    assert shell.do_EOF("") is True


def test_emptyline_returns_false(shell):
    assert shell.emptyline() is False


def test_default_unknown_command(shell):
    shell.default("xyzzy")
    assert "Unknown command: 'xyzzy'" in shell.console.export_text()


def test_do_info_shows_paths(shell):
    # Windows temp paths are long (C:\Users\...\pytest-of-\...\config.yaml);
    # rich folds an unbreakable word mid-filename when the panel is narrower
    # than the path, which would split "config.yaml". Widen for this test.
    shell.console.width = 160
    shell.do_info("")
    text = shell.console.export_text()
    assert "AgentBench-SE Information" in text
    assert "config.yaml" in text


def test_do_config_show_masks_api_key(shell):
    shell.do_config("show")
    text = shell.console.export_text()
    assert "sk-super" in text          # first 8 chars shown
    assert "sk-super-secret-key" not in text  # full key never leaked


def test_do_config_set_updates_file(shell):
    shell.do_config("set provider.model llama-3.3-70b")
    assert "Updated provider.model = llama-3.3-70b" in shell.console.export_text()
    assert shell.config["provider"]["model"] == "llama-3.3-70b"


def test_do_config_set_unknown_path(shell):
    shell.do_config("set provider.nope x")
    assert "Unknown config path" in shell.console.export_text()


def test_do_run_accepts_flags(shell, monkeypatch):
    """do_run delegates to RunCommand; unknown flag surfaces a validation error."""
    from rich.prompt import Confirm

    monkeypatch.setattr(Confirm, "ask", lambda *a, **k: False)
    shell.do_run("--issues 999")
    assert "must be between 1 and 50" in shell.console.export_text()