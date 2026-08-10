"""Tests for the RunCommand (Phase 3).

Uses a mock provider factory + a stubbed issue loader so no real API keys,
network calls, or dataset downloads are needed.
"""

from pathlib import Path

import pytest
from rich.console import Console

from agentbench.commands.run import RunCommand, VALID_STRATEGIES, DEFAULT_ISSUES

CONFIG = {
    "researcher": {"name": "Agi"},
    "provider": {"name": "openrouter", "api_key": "sk-x", "model": "m"},
    "experiment": {"temperature": 0.2, "max_retries": 3, "rate_limit": 0.0,
                   "usd_idr_rate": 16500.0},
}


class FakeProvider:
    def __init__(self):
        self.health_ok = True

    def health_check(self):
        return self.health_ok


class FakeStrategy:
    def __init__(self, provider):
        self.provider = provider


@pytest.fixture
def console():
    return Console(force_terminal=True, width=100, record=True)


@pytest.fixture
def command(console):
    return RunCommand(
        CONFIG,
        console,
        provider_factory=lambda: FakeProvider(),
        issue_loader=lambda: _fake_issues(2),
    )


@pytest.fixture(autouse=True)
def no_network_rate(monkeypatch):
    """Keep tests offline: silence live rate fetch by default."""
    original = RunCommand._refresh_rate

    def fake_refresh(self):
        self.config.setdefault("experiment", {})["usd_idr_rate"] = 17000.0

    monkeypatch.setattr(RunCommand, "_refresh_rate", fake_refresh)
    yield original
    # restore happens automatically via monkeypatch teardown


def _fake_issues(n):
    from agentbench.core.models.issue import Issue

    return [
        Issue(
            instance_id=f"django__django-{1000 + i}",
            repo="django/django",
            base_commit="abc",
            problem_statement="bug",
        )
        for i in range(n)
    ]


# --------------------------------------------------------------------- #
# Resolution helpers
# --------------------------------------------------------------------- #
def test_default_issues_when_omitted(command):
    assert command._resolve_issues({}) == DEFAULT_ISSUES


def test_issues_parsed_from_flag(command):
    assert command._resolve_issues({"issues": "10"}) == 10


def test_issues_out_of_range_rejected(command):
    with pytest.raises(ValueError):
        command._resolve_issues({"issues": "99"})
    with pytest.raises(ValueError):
        command._resolve_issues({"issues": "0"})


def test_issues_invalid_rejected(command):
    with pytest.raises(ValueError):
        command._resolve_issues({"issues": "abc"})


def test_strategy_all_default(command):
    assert command._resolve_strategy({}) == "all"


def test_strategy_valid_value(command):
    assert command._resolve_strategy({"strategy": "direct"}) == "direct"


def test_strategy_unknown_rejected(command):
    with pytest.raises(ValueError):
        command._resolve_strategy({"strategy": "nope"})


def test_output_default_is_results(command):
    """Default output is the base dir; runner appends EXP-<id> itself."""
    assert command._resolve_output({}) == "results"


def test_output_from_flag(command):
    assert command._resolve_output({"output": "myout"}) == "myout"


# --------------------------------------------------------------------- #
# execute() flow
# --------------------------------------------------------------------- #
def test_execute_aborts_on_bad_flag(command):
    command.execute("--issues 999")
    assert "must be between 1 and 50" in command.console.export_text()


def test_execute_aborts_when_user_declines(monkeypatch, command):
    from rich.prompt import Confirm

    monkeypatch.setattr(Confirm, "ask", lambda *a, **k: False)
    command.execute("--issues 2 --strategy direct")
    text = command.console.export_text()
    assert "Aborted." in text


def test_execute_full_run_with_mock_runner(monkeypatch, command):
    """Happy path: confirmation yes, runner invoked with callback."""
    from rich.prompt import Confirm
    import pandas as pd

    monkeypatch.setattr(Confirm, "ask", lambda *a, **k: True)

    calls = {}

    def fake_run_experiments(issues, strategies, **kwargs):
        calls["issues"] = len(issues)
        calls["strategies"] = list(strategies.keys())
        calls["callback"] = kwargs.get("on_issue_complete")
        # simulate one completed issue via callback
        kwargs["on_issue_complete"](
            instance_id="django__django-1000",
            strategy="direct",
            elapsed=1.2,
            tokens=100,
            cost_usd=0.001,
            success=True,
            status="VALID",
        )
        df = pd.DataFrame([{
            "instance_id": "django__django-1000", "strategy": "direct",
            "total_tokens": 100, "cost_usd": 0.001, "success": 1,
        }])
        return df, "EXP-test"

    monkeypatch.setattr(
        "agentbench.core.experiments.runner.run_experiments", fake_run_experiments
    )

    command.execute("--issues 2 --strategy direct")
    text = command.console.export_text()

    assert calls["issues"] == 2
    assert calls["strategies"] == ["direct"]
    assert calls["callback"] is not None
    assert "Experiment completed: EXP-test" in text
    assert "Success: 100.0%" in text


def test_execute_health_check_failure(monkeypatch, command):
    from rich.prompt import Confirm

    monkeypatch.setattr(Confirm, "ask", lambda *a, **k: True)
    command._provider_factory = lambda: FakeProvider()  # health_ok True default

    # force failure
    def bad_provider():
        p = FakeProvider()
        p.health_ok = False
        return p

    command._provider_factory = bad_provider
    command.execute("--issues 1")
    assert "health check failed" in command.console.export_text().lower()


def test_build_strategies_all(monkeypatch, command):
    provider, strategies = command._build_strategies("all")
    assert set(strategies.keys()) == {"direct", "planning", "review"}
    assert provider is not None


def test_build_strategies_single(command):
    provider, strategies = command._build_strategies("direct")
    assert set(strategies.keys()) == {"direct"}


def test_agent_manifest_uses_registry(monkeypatch, command):
    monkeypatch.setattr(
        "agentbench.core.agents.registry.build_agent_team",
        lambda provider: {
            "direct": type("A", (), {"prompt_file": "direct.md"})(),
        },
    )
    manifest = command._agent_manifest(FakeProvider())
    assert manifest == [{"name": "direct", "prompt_file": "direct.md"}]


# --------------------------------------------------------------------- #
# experiment.yaml snapshot
# --------------------------------------------------------------------- #
def test_save_experiment_config_writes_yaml(tmp_path):
    from agentbench.core.experiments.experiment_config import (
        save_experiment_config,
    )

    path = save_experiment_config(
        str(tmp_path),
        researcher={"name": "Agi", "institution": "UNJ"},
        provider={"name": "openrouter", "model": "tencent/hy3:free"},
        experiment={"temperature": 0.2, "max_retries": 3, "usd_idr_rate": 16500.0},
        issue_count=5,
        strategy_names=["direct", "planning"],
        experiment_id="EXP-1",
        agents=[{"name": "direct", "prompt_file": "direct.md"}],
    )
    assert path == str(tmp_path / "experiment.yaml")
    assert (tmp_path / "experiment.yaml").exists()

    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert data["experiment"]["id"] == "EXP-1"
    assert data["experiment"]["researcher"] == "Agi"
    assert data["provider"]["model"] == "tencent/hy3:free"
    assert data["dataset"]["n_issues"] == 5
    assert data["strategies"] == ["direct", "planning"]


def test_save_experiment_config_uses_defaults(tmp_path):
    from agentbench.core.experiments.experiment_config import (
        save_experiment_config,
    )

    path = save_experiment_config(str(tmp_path), issue_count=2)
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert data["experiment"]["researcher"] == "Agi Rahman Setiadi"
    assert data["provider"]["model"] == "unknown"


def test_execute_writes_experiment_yaml(monkeypatch, command, tmp_path):
    """Full run through execute() must produce experiment.yaml."""
    from rich.prompt import Confirm
    import pandas as pd

    monkeypatch.setattr(Confirm, "ask", lambda *a, **k: True)

    def fake_run_experiments(issues, strategies, **kwargs):
        df = pd.DataFrame([{
            "instance_id": "x", "strategy": "direct", "total_tokens": 10,
            "cost_usd": 0.001, "success": 1,
        }])
        return df, "EXP-SNAP"

    monkeypatch.setattr(
        "agentbench.core.experiments.runner.run_experiments", fake_run_experiments
    )
    out = str(tmp_path / "out")
    command.execute(f"--issues 1 --strategy direct --output {out}")
    text = command.console.export_text()
    assert "Config snapshot" in text
    assert (tmp_path / "out" / "EXP-SNAP" / "experiment.yaml").exists()


# --------------------------------------------------------------------- #
# console silencing (progress bar cleanliness)
# --------------------------------------------------------------------- #
def test_silence_console_removes_stderr_sink():
    from agentbench.core.utils.logger import (
        logger,
        silence_console,
        restore_console,
        stderr_sink_ids,
    )

    stderr_sink_ids.clear()
    # ensure at least one stderr sink exists to silence
    restore_console()
    assert silence_console() is True
    # after silencing, no stderr sink is tracked
    assert stderr_sink_ids == set()
    # restore re-adds and tracks it
    restore_console()
    assert stderr_sink_ids != set()
    # silence again for a clean end state
    silence_console()


def test_silence_console_idempotent_when_no_sink():
    from agentbench.core.utils.logger import (
        silence_console,
        restore_console,
        stderr_sink_ids,
    )

    stderr_sink_ids.clear()
    silence_console()  # nothing to remove
    # remove the tracked sink directly so the next call is a no-op
    silence_console()
    restore_console()  # clean up
    silence_console()  # leave clean


# --------------------------------------------------------------------- #
# live USD/IDR rate
# --------------------------------------------------------------------- #
def test_refresh_rate_updates_config(monkeypatch, command, no_network_rate):
    """_refresh_rate persists the fetched rate to config and logs source."""
    original = no_network_rate
    monkeypatch.setattr(RunCommand, "_refresh_rate", original)
    monkeypatch.setattr(
        "agentbench.exchange_rate.fetch_usd_idr_rate",
        lambda use_cache=True: (17900.0, "bi.jisdor", "2026-08-06T00:00:00+00:00"),
    )
    command._refresh_rate()
    assert command.config["experiment"]["usd_idr_rate"] == 17900.0
    text = command.console.export_text()
    assert "USD/IDR: 17,900" in text
    assert "bi.jisdor" in text


def test_refresh_rate_failure_keeps_config(monkeypatch, command, no_network_rate):
    from agentbench.exchange_rate import RateFetchError

    original = no_network_rate
    monkeypatch.setattr(RunCommand, "_refresh_rate", original)

    def boom(*a, **k):
        raise RateFetchError("all sources down")

    import agentbench.exchange_rate as er_mod

    monkeypatch.setattr(er_mod, "fetch_usd_idr_rate", boom)
    command._refresh_rate()
    text = command.console.export_text()
    assert "Could not fetch live USD/IDR rate" in text


# --------------------------------------------------------------------- #
# non-interactive (TUI) mode: no stdin prompts, no rich progress flood
# --------------------------------------------------------------------- #
def test_non_interactive_skips_confirm(monkeypatch, console, tmp_path):
    """TUI mode must not call Confirm.ask (stdin belongs to Textual)."""
    import agentbench.commands.run as run_mod
    from agentbench.commands.run import RunCommand
    import pandas as pd

    called = {"confirm": 0}

    def fake_confirm(*a, **k):
        called["confirm"] += 1
        return True

    monkeypatch.setattr(run_mod, "Confirm", type("C", (), {"ask": staticmethod(fake_confirm)})())

    def fake_runner(issues, strategies, **kw):
        kw["on_issue_complete"](
            instance_id="django__django-1", strategy="direct", elapsed=0.5,
            tokens=100, cost_usd=0.001, success=True, status="VALID",
        )
        return pd.DataFrame(), "EXP-TUI"

    monkeypatch.setattr(
        "agentbench.core.experiments.runner.run_experiments", fake_runner
    )
    monkeypatch.setattr(
        "agentbench.core.dataset_loader.select_issues", lambda: _fake_issues(1)
    )

    cmd = RunCommand(CONFIG, console, interactive=False)
    cmd._default_provider = lambda: FakeProvider()
    cmd._refresh_rate = lambda: None
    cmd._save_config_snapshot = lambda *a, **k: None
    cmd._print_summary = lambda *a, **k: None
    cmd.execute("--issues 1 --strategy direct --output /tmp/ab-tui-run")

    assert called["confirm"] == 0  # no prompt shown in TUI mode
    text = console.export_text()
    assert "VALID" in text  # simple reporter line landed