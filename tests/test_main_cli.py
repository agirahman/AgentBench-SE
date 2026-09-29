import sys
import types

import pytest
import yaml

from main import _PROVIDER_MODEL_MAP, _save_experiment_config, parse_args
from config import Config
from evaluation.cost import PricingTable


def test_parse_args_accepts_repo_spec_override(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "main.py",
            "--provider",
            "openrouter",
            "--issues",
            "3",
            "--repo-spec",
            "django/django=2",
            "--repo-spec",
            "psf/requests=1",
        ],
    )

    args = parse_args()

    assert args.provider == "openrouter"
    assert args.issues == 3
    assert args.repo_spec == ["django/django=2", "psf/requests=1"]
    assert args.instance_ids is None, "targeted mode must be opt-in"


def test_parse_args_accepts_instance_ids(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "main.py",
            "--provider",
            "opencode",
            "--instance-ids",
            "django__django-11001",
            "--strategies",
            "review",
        ],
    )

    args = parse_args()

    assert args.instance_ids == ["django__django-11001"]
    assert args.strategies == ["review"]


def test_save_experiment_config_records_targeted_instance_ids(tmp_path):
    """A targeted run must be reproducible from its own experiment.yaml.

    Without the ids recorded, the file would only say "1 issue in django/django"
    and the exact instance would have to be guessed from the artifacts.
    """
    _save_experiment_config(
        str(tmp_path),
        types.SimpleNamespace(provider="deepseek", instance_ids=["django__django-11001"]),
        issue_count=1,
        strategy_names=["review"],
        experiment_id="EXP-test-002",
        agents=[],
        repos={"django/django": 1},
    )
    cfg = yaml.safe_load((tmp_path / "experiment.yaml").read_text(encoding="utf-8"))
    assert cfg["dataset"]["instance_ids"] == ["django__django-11001"]
    assert cfg["tool_calling"]["revision_tool_turns"] == Config.REVISION_TOOL_TURNS


def test_save_experiment_config_without_instance_ids_stays_none(tmp_path):
    """The full-suite path must not gain a spurious instance_ids field.

    ``_save_experiment_config`` is also called by older tooling that passes a
    hand-built namespace with no ``instance_ids`` attribute at all — that must
    not raise (it did, and this test is the regression guard).
    """
    _save_experiment_config(
        str(tmp_path),
        types.SimpleNamespace(provider="deepseek"),
        issue_count=3,
        strategy_names=["direct"],
        experiment_id="EXP-test-003",
        agents=[],
        repos={"django/django": 3},
    )
    cfg = yaml.safe_load((tmp_path / "experiment.yaml").read_text(encoding="utf-8"))
    assert cfg["dataset"]["instance_ids"] is None


def test_provider_model_map_covers_all_providers():
    assert set(_PROVIDER_MODEL_MAP) == {
        "gemini",
        "groq",
        "openrouter",
        "opencode",
        "deepseek",
        "commandcode",
    }
    assert _PROVIDER_MODEL_MAP["gemini"] == Config.GEMINI_MODEL
    assert _PROVIDER_MODEL_MAP["groq"] == Config.GROQ_MODEL
    assert _PROVIDER_MODEL_MAP["openrouter"] == Config.OPENROUTER_MODEL
    assert _PROVIDER_MODEL_MAP["opencode"] == Config.OPENCODE_MODEL
    assert _PROVIDER_MODEL_MAP["deepseek"] == Config.DEEPSEEK_MODEL
    assert _PROVIDER_MODEL_MAP["commandcode"] == Config.COMMANDCODE_MODEL
    assert all(_PROVIDER_MODEL_MAP.values())


def test_save_experiment_config_deepseek_model_and_pricing(tmp_path):
    _save_experiment_config(
        str(tmp_path),
        types.SimpleNamespace(provider="deepseek"),
        issue_count=1,
        strategy_names=["direct", "planning", "review"],
        experiment_id="EXP-test-001",
        agents=[],
        repos={"psf/requests": 1},
    )
    cfg = yaml.safe_load((tmp_path / "experiment.yaml").read_text(encoding="utf-8"))

    model = Config.DEEPSEEK_MODEL
    assert cfg["provider"]["name"] == "deepseek"
    assert cfg["provider"]["model"] == model

    off = PricingTable.rates_for(model, "off_peak")
    peak = PricingTable.rates_for(model, "peak")
    assert cfg["pricing"]["model"] == model
    assert cfg["pricing"]["pricing_source"] == "https://api-docs.deepseek.com/quick_start/pricing"
    assert cfg["pricing"]["off_peak_per_1m_tokens"] == {
        "input_regular": off["input_per_million"],
        "input_cache_hit": off["cached_input_per_million"],
        "output": off["output_per_million"],
    }
    assert cfg["pricing"]["peak_per_1m_tokens"] == {
        "input_regular": peak["input_per_million"],
        "input_cache_hit": peak["cached_input_per_million"],
        "output": peak["output_per_million"],
    }
    assert cfg["model_thinking"] == Config.DEEPSEEK_THINKING
    assert cfg["model_reasoning_effort"] == Config.DEEPSEEK_REASONING_EFFORT


def test_save_experiment_config_unknown_provider_exits(tmp_path):
    args = types.SimpleNamespace(provider="not-a-provider")
    with pytest.raises(SystemExit) as exc:
        _save_experiment_config(
            str(tmp_path),
            args,
            issue_count=1,
            strategy_names=[],
            experiment_id="EXP-x",
            agents=[],
        )
    assert exc.value.code == 1
