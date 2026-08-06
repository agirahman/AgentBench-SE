"""Tests for ConfigManager. Uses an isolated temp config dir per test."""

import os

import pytest
import yaml

from agentbench.config_manager import (
    ConfigManager,
    ConfigError,
    _coerce,
)

VALID_CONFIG = {
    "researcher": {"name": "Agi", "institution": "UNJ", "email": "a@b.com"},
    "provider": {"name": "openrouter", "api_key": "sk-x", "model": "tencent/hy3:free"},
    "experiment": {"temperature": 0.2, "max_retries": 3, "rate_limit": 1.5,
                   "usd_idr_rate": 16500.0},
}


@pytest.fixture
def cm(tmp_path):
    return ConfigManager(config_path=tmp_path / "config.yaml")


@pytest.fixture
def cm_with_config(tmp_path):
    m = ConfigManager(config_path=tmp_path / "config.yaml")
    m.save(VALID_CONFIG)
    return m


def test_config_exists_false_when_missing(cm):
    assert cm.config_exists() is False


def test_load_raises_when_missing(cm):
    with pytest.raises(ConfigError):
        cm.load()


def test_save_and_load_roundtrip(cm):
    path = cm.save(VALID_CONFIG)
    assert path.exists()
    assert cm.load() == VALID_CONFIG
    with open(path, encoding="utf-8") as f:
        assert "openrouter" in f.read()


def test_corrupted_yaml_raises(cm):
    cm.config_path.write_text("{{{{ not yaml", encoding="utf-8")
    with pytest.raises(ConfigError):
        cm.load()


def test_validate_requires_researcher_name(cm):
    bad = dict(VALID_CONFIG, researcher={})
    with pytest.raises(ConfigError, match="researcher.name"):
        cm.validate(bad)


def test_validate_rejects_unknown_provider(cm):
    bad = dict(VALID_CONFIG, provider=dict(VALID_CONFIG["provider"], name="foo"))
    with pytest.raises(ConfigError, match="openrouter|gemini|groq|opencode"):
        cm.validate(bad)


def test_validate_requires_api_key_when_flagged(cm):
    bad = dict(VALID_CONFIG, provider=dict(VALID_CONFIG["provider"], api_key=""))
    with pytest.raises(ConfigError, match="api_key"):
        cm.validate(bad, require_api_key=True)


def test_validate_rejects_out_of_range_temperature(cm):
    bad = dict(VALID_CONFIG, experiment=dict(VALID_CONFIG["experiment"],
                                             temperature=2.0))
    with pytest.raises(ConfigError):
        cm.validate(bad)


def test_set_value_dot_notation(cm_with_config):
    m = cm_with_config
    out = m.set_value("provider.model", "llama-3.3-70b")
    assert out["provider"]["model"] == "llama-3.3-70b"
    assert m.load()["provider"]["model"] == "llama-3.3-70b"


def test_set_value_unknown_path_raises(cm_with_config):
    with pytest.raises(ConfigError, match="Unknown config path"):
        cm_with_config.set_value("provider.nonexistent", "x")


def test_set_value_coerces_numbers(cm_with_config):
    out = cm_with_config.set_value("experiment.temperature", "0.7")
    assert out["experiment"]["temperature"] == 0.7


def test_reset_removes_file(cm_with_config):
    m = cm_with_config
    assert m.config_exists()
    m.reset()
    assert not m.config_exists()


def test_as_dict_returns_empty_when_missing(cm):
    assert cm.as_dict() == {}


def test_config_dir_env_override(tmp_path):
    os.environ["AGENTBENCH_CONFIG_DIR"] = str(tmp_path)
    try:
        m = ConfigManager()
        assert m.config_dir == tmp_path
    finally:
        del os.environ["AGENTBENCH_CONFIG_DIR"]


def test_coerce():
    assert _coerce("true") is True
    assert _coerce("3") == 3
    assert _coerce("1.5") == 1.5
    assert _coerce("abc") == "abc"


# --------------------------------------------------------------------- #
# config rate (live USD/IDR)
# --------------------------------------------------------------------- #
def test_config_rate_refreshes(monkeypatch, cm_with_config):
    import agentbench.commands.config as cfg_mod
    from rich.console import Console

    monkeypatch.setattr(
        "agentbench.exchange_rate.fetch_usd_idr_rate",
        lambda **k: (17900.0, "bi.jisdor", "2026-08-06T00:00:00+00:00"),
    )
    console = Console(force_terminal=True, width=100, record=True)
    command = cfg_mod.ConfigCommand(cm_with_config.load(), console, cm_with_config)
    command.execute("rate")
    # persisted via ConfigManager.set_value -> loaded back with new rate
    assert cm_with_config.load()["experiment"]["usd_idr_rate"] == 17900.0