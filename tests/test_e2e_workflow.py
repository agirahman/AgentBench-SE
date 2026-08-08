"""End-to-end workflow test: setup -> shell -> run -> results -> export.

Uses mocks for the provider/dataset/runner layers (no API keys, no network),
but drives the REAL CLI entry point, shell, and command classes.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def cli_env(tmp_path):
    """Environment where AGENTBENCH_CONFIG_DIR is isolated per test."""
    env = {**os.environ, "PYTHONPATH": str(REPO),
           "AGENTBENCH_CONFIG_DIR": str(tmp_path),
           # Windows pipes default to the locale codec (cp1252), which cannot
           # encode the wizard's Unicode glyphs (e.g. ✎). Force UTF-8 so the
           # subprocess CLI behaves identically on every platform.
           "PYTHONUTF8": "1"}
    return env, tmp_path


def _run_cli(args, stdin, env):
    return subprocess.run(
        [sys.executable, "-m", "agentbench.cli.main", *args],
        input=stdin, text=True, capture_output=True, cwd=REPO, env=env,
        # The subprocess runs with PYTHONUTF8=1 and emits UTF-8; the parent
        # pytest process may default to the locale codec (cp1252 on Windows)
        # and crash decoding the wizard's Unicode glyphs otherwise.
        encoding="utf-8", errors="replace",
    )


def test_full_workflow_setup_shell_results_export(cli_env):
    env, tmp_path = cli_env

    # 1) setup
    setup_input = "\n".join([
        "Agi E2E", "UNJ", "agi@test.com", "openrouter", "sk-e2e-key-123",
        "model-e2e", "0.2", "3", "1.5", "16500", "n",
    ]) + "\n"
    r = _run_cli(["setup"], setup_input, env)
    assert r.returncode == 0, r.stderr
    cfg = tmp_path / "config.yaml"
    assert cfg.exists()

    # 2) shell session with mocked run pipeline
    session = r"""
import sys, os
sys.path.insert(0, %r)
import pandas as pd
from rich.prompt import Confirm
Confirm.ask = lambda *a, **k: True

from agentbench.core.experiments import runner as runner_mod
_E2E = {"df": None}
def fake_run(issues, strategies, **kwargs):
    cb = kwargs["on_issue_complete"]
    for i, issue in enumerate(issues):
        for sname in strategies:
            cb(instance_id=issue.instance_id, strategy=sname, elapsed=0.5,
               tokens=800, cost_usd=0.001, success=True, status="VALID")
    rows = [{"instance_id": i.instance_id, "strategy": s,
             "execution_time": 0.5, "inference_count": 1,
             "prompt_tokens": 400, "completion_tokens": 400,
             "total_tokens": 800, "cost_usd": 0.001, "success": 1,
             "patch_status": "VALID", "error": ""}
            for i in issues for s in strategies]
    _E2E["df"] = pd.DataFrame(rows)
    return _E2E["df"], "EXP-E2E"
runner_mod.run_experiments = fake_run

from agentbench.commands import results as results_mod
def _load_results(csv_path=None):
    return _E2E["df"]
results_mod.load_results = _load_results

from agentbench.core.dataset_loader import select_issues
class FakeIssue:
    def __init__(self, i):
        self.instance_id = f"django__django-{1000+i}"
        self.repo = "django/django"
        self.base_commit = "abc"
        self.problem_statement = "bug"
        self.difficulty = "easy"
select_issues = lambda: [FakeIssue(0), FakeIssue(1)]

from agentbench.core.agents.registry import build_agent_team
build_agent_team = lambda provider: {"direct": type("A", (), {"prompt_file": "x"})()}

from agentbench.commands.run import RunCommand
RunCommand._default_provider = lambda self: type("P", (), {"health_check": lambda s: True})()

from agentbench.shell import AgentBenchShell
from rich.console import Console

shell = AgentBenchShell(console=Console(record=True))
out_path = %r
for line in [
    "run --issues 2 --strategy all --output /tmp/e2e-results",
    "results summary",
    "results errors",
    f"export --format markdown --output {out_path}",
    "exit",
]:
    shell.onecmd(line)
text = shell.console.export_text()
print("HAS_SUMMARY:", "Results Summary" in text)
print("HAS_EXP:", "EXP-E2E" in text)
print("EXPORT_EXISTS:", os.path.exists(out_path))
""" % (str(REPO), str(tmp_path / "export.md"))
    r = subprocess.run([sys.executable, "-c", session], cwd=REPO,
                       capture_output=True, text=True, env=env,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr[-2000:]
    assert "HAS_SUMMARY: True" in r.stdout
    assert "HAS_EXP: True" in r.stdout
    assert "EXPORT_EXISTS: True" in r.stdout

    # 3) exported markdown is well-formed
    md = (tmp_path / "export.md").read_text(encoding="utf-8")
    assert "AgentBench-SE Experiment Results" in md
    assert "| strategy |" in md

    # 4) legacy shim still runs
    r = subprocess.run([sys.executable, "src/main.py", "--help"], cwd=REPO,
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert "AgentBench-SE Experiment Runner" in r.stdout