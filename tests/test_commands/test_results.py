"""Tests for ResultsCommand and ExportCommand."""

from pathlib import Path

import pandas as pd
import pytest
from rich.console import Console

from agentbench.commands.results import (
    ResultsCommand,
    find_latest_results_csv,
    load_results,
)
from agentbench.commands.export import ExportCommand

CONFIG = {"researcher": {"name": "Agi"}, "provider": {"name": "openrouter"},
          "experiment": {"rate_limit": 1.5}}

CSV_HEADER = [
    "instance_id", "strategy", "execution_time", "inference_count",
    "prompt_tokens", "completion_tokens", "total_tokens",
    "patch_preview", "error", "patch_status",
]


@pytest.fixture
def results_csv(tmp_path):
    rows = [
        ["django__django-1", "direct", 2.5, 1, 300, 600, 900, "--- a/x\n+++ b/x\n+fix", "", "VALID"],
        ["django__django-1", "planning", 3.5, 2, 900, 700, 1600, "--- a/x\n+++ b/x\n+fix2", "", "VALID"],
        ["django__django-1", "review", 4.0, 3, 1500, 900, 2400, "--- a/x\n+++ b/x\n+fix3", "", "MALFORMED_HEADER"],
        ["django__django-2", "direct", 1.0, 1, 200, 400, 600, "", "timeout", "TIMEOUT"],
    ]
    df = pd.DataFrame(rows, columns=CSV_HEADER)
    path = tmp_path / "results.csv"
    df.to_csv(path, index=False)
    return str(path)


@pytest.fixture
def console():
    return Console(force_terminal=True, width=120, record=True)


# --------------------------------------------------------------------- #
# ResultsCommand
# --------------------------------------------------------------------- #
def test_summary_renders_table(results_csv, console):
    ResultsCommand(CONFIG, console, csv_path=results_csv).execute("summary")
    text = console.export_text()
    assert "Results Summary" in text
    assert "direct" in text
    assert "planning" in text
    assert "review" in text


def test_compare_renders_rows(results_csv, console):
    ResultsCommand(CONFIG, console, csv_path=results_csv).execute("compare")
    text = console.export_text()
    assert "Strategy Comparison" in text
    assert "django__django-1" in text


def test_errors_shows_only_failed(results_csv, console):
    ResultsCommand(CONFIG, console, csv_path=results_csv).execute("errors")
    text = console.export_text()
    assert "django__django-2" in text
    assert "timeout" in text


def test_errors_none_when_clean(console):
    import tempfile, os

    d = tempfile.mkdtemp()
    p = os.path.join(d, "c.csv")
    pd.DataFrame([
        ["django__django-1", "direct", 1.0, 1, 100, 200, 300, "patch", "", "VALID"],
    ], columns=CSV_HEADER).to_csv(p, index=False)
    ResultsCommand(CONFIG, console, csv_path=p).execute("errors")
    assert "No errors found" in console.export_text()


def test_patch_found(results_csv, console):
    ResultsCommand(CONFIG, console, csv_path=results_csv).execute(
        "patch django__django-1 --strategy direct"
    )
    text = console.export_text()
    assert "django__django-1 / direct" in text
    assert "+fix" in text


def test_patch_not_found(results_csv, console):
    ResultsCommand(CONFIG, console, csv_path=results_csv).execute(
        "patch django__nope"
    )
    assert "No patch found" in console.export_text()


def test_unknown_subcommand(results_csv, console):
    ResultsCommand(CONFIG, console, csv_path=results_csv).execute("bogus")
    assert "Unknown subcommand" in console.export_text()


def test_load_missing_file_errors_gracefully(console):
    ResultsCommand(CONFIG, console, csv_path="/nonexistent/x.csv").execute("summary")
    assert "No results found" in console.export_text()


def test_find_latest_results_csv(tmp_path):
    (tmp_path / "EXP-001").mkdir()
    (tmp_path / "EXP-002").mkdir()
    (tmp_path / "EXP-001" / "results.csv").write_text("a,b\n1,2")
    (tmp_path / "EXP-002" / "results.csv").write_text("a,b\n3,4")
    latest = find_latest_results_csv(str(tmp_path))
    assert latest is not None
    assert "EXP-002" in latest  # newest mtime


# --------------------------------------------------------------------- #
# ExportCommand
# --------------------------------------------------------------------- #
def test_export_csv(results_csv, console, tmp_path):
    out = str(tmp_path / "out.csv")
    ExportCommand(CONFIG, console, csv_path=results_csv).execute(f"--format csv --output {out}")
    assert "Exported to" in console.export_text()
    re_df = pd.read_csv(out)
    assert len(re_df) == 4
    assert "strategy" in re_df.columns


def test_export_json(results_csv, console, tmp_path):
    out = str(tmp_path / "out.json")
    ExportCommand(CONFIG, console, csv_path=results_csv).execute(f"--format json --output {out}")
    import json

    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert data["row_count"] == 4
    assert set(data["strategies"]) == {"direct", "planning", "review"}


def test_export_markdown(results_csv, console, tmp_path):
    out = str(tmp_path / "out.md")
    ExportCommand(CONFIG, console, csv_path=results_csv).execute(f"--format markdown --output {out}")
    text = Path(out).read_text(encoding="utf-8")
    assert "AgentBench-SE Experiment Results" in text
    assert "| instance_id |" in text


def test_export_unknown_format(results_csv, console):
    ExportCommand(CONFIG, console, csv_path=results_csv).execute("--format xml")
    assert "Unknown format" in console.export_text()


def test_export_default_output_path(results_csv, console, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    ExportCommand(CONFIG, console, csv_path=results_csv).execute("--format csv")
    text = console.export_text()
    assert "Exported to" in text
    # file created under results/
    assert (tmp_path / "results").exists()