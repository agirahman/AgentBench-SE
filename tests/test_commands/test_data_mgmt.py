"""Tests for Phase 5 data-management commands (provider/dataset/artifacts)."""

import json

import pytest
from rich.console import Console

from agentbench.commands.provider import ProviderCommand, mask_key
from agentbench.commands.dataset import DatasetCommand
from agentbench.commands.artifacts import ArtifactsCommand

CONFIG = {
    "researcher": {"name": "Agi"},
    "provider": {"name": "openrouter", "api_key": "sk-super-secret", "model": "m1"},
    "experiment": {"temperature": 0.2},
}


@pytest.fixture
def console():
    return Console(force_terminal=True, width=120, record=True)


# --------------------------------------------------------------------- #
# Provider
# --------------------------------------------------------------------- #
def test_provider_show_masks_key(console):
    ProviderCommand(CONFIG, console).execute("")
    text = console.export_text()
    assert "openrouter" in text
    assert "sk-super" in text
    assert "sk-super-secret" not in text  # never leaks full key


def test_provider_show_no_args_has_hint(console):
    ProviderCommand(CONFIG, console).execute("")
    assert "provider --test" in console.export_text()


def test_provider_test_success(console, monkeypatch):
    monkeypatch.setattr(
        "agentbench.provider_health.check_connection",
        lambda *a, **k: (True, "We got reply: 'OK'"),
    )
    ProviderCommand(CONFIG, console).execute("--test")
    assert "Provider online" in console.export_text()


def test_provider_test_failure(console, monkeypatch):
    monkeypatch.setattr(
        "agentbench.provider_health.check_connection",
        lambda *a, **k: (False, "boom"),
    )
    ProviderCommand(CONFIG, console).execute("--test")
    assert "Provider error: boom" in console.export_text()


def test_mask_key():
    assert mask_key("sk-abcdefgh-123") == "sk-abcde***"
    assert mask_key("") == "(not set)"


# --------------------------------------------------------------------- #
# Dataset
# --------------------------------------------------------------------- #
def test_dataset_not_cached_warns(console, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBENCH_CONFIG_DIR", str(tmp_path))
    DatasetCommand(CONFIG, console).execute("")
    assert "not cached" in console.export_text()


def test_dataset_refresh_writes_cache(console, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBENCH_CONFIG_DIR", str(tmp_path))

    fake_issues = [
        type("I", (), {"instance_id": f"repo__x-{i}", "repo": "django/django",
                       "difficulty": "easy" if i % 2 == 0 else "hard"})()
        for i in range(4)
    ]
    monkeypatch.setattr(
        "agentbench.core.dataset_loader.select_issues", lambda: fake_issues
    )
    DatasetCommand(CONFIG, console).execute("--refresh")
    text = console.export_text()
    assert "Dataset refreshed" in text
    assert "django/django" in text
    # rich wraps the table title; check the data cells instead
    assert "easy" in text and "hard" in text

    cache_file = tmp_path / "cache" / "swe-bench-lite" / "dataset.json"
    assert cache_file.exists()
    data = json.loads(cache_file.read_text())
    assert len(data) == 4


def test_dataset_reads_cache(console, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBENCH_CONFIG_DIR", str(tmp_path))
    cache = tmp_path / "cache" / "swe-bench-lite"
    cache.mkdir(parents=True)
    (cache / "dataset.json").write_text(json.dumps([
        {"instance_id": "a", "repo": "sympy/sympy", "difficulty": "medium"},
        {"instance_id": "b", "repo": "sympy/sympy", "difficulty": "hard"},
    ]))
    DatasetCommand(CONFIG, console).execute("")
    text = console.export_text()
    assert "sympy/sympy" in text
    assert "medium" in text


# --------------------------------------------------------------------- #
# Artifacts
# --------------------------------------------------------------------- #
def test_artifacts_usage_error(console):
    ArtifactsCommand(CONFIG, console).execute("only-one")
    assert "Usage: artifacts" in console.export_text()


def test_artifacts_not_found(console, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agentbench.commands.artifacts._find_latest_exp_dir",
        lambda: tmp_path,
    )
    ArtifactsCommand(CONFIG, console).execute("django__x direct")
    assert "No artifacts for django__x / direct" in console.export_text()


def test_artifacts_renders_files(console, tmp_path, monkeypatch):
    exp = tmp_path / "EXP-1"
    art = exp / "artifacts" / "django__django-1" / "direct"
    art.mkdir(parents=True)
    (art / "planner.md").write_text("# Plan\n\nstep 1")
    (art / "patch.txt").write_text("--- a/x\n+++ b/x\n+fix")

    monkeypatch.setattr(
        "agentbench.commands.artifacts._find_latest_exp_dir",
        lambda: exp,
    )
    ArtifactsCommand(CONFIG, console).execute("django__django-1 direct")
    text = console.export_text()
    assert "planner.md" in text
    assert "patch.txt" in text
    assert "step 1" in text


def test_artifacts_no_experiments(console, tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    monkeypatch.setattr(
        "agentbench.commands.artifacts._find_latest_exp_dir", lambda: None
    )
    ArtifactsCommand(CONFIG, console).execute("django__x direct")
    assert "No experiment found" in console.export_text()