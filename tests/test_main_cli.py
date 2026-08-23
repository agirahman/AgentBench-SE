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


def test_provider_model_map_covers_all_providers():
    assert set(_PROVIDER_MODEL_MAP) == {
        "gemini",
        "groq",
        "openrouter",
        "opencode",
        "deepseek",
    }
    assert _PROVIDER_MODEL_MAP["gemini"] == Config.GEMINI_MODEL
    assert _PROVIDER_MODEL_MAP["groq"] == Config.GROQ_MODEL
    assert _PROVIDER_MODEL_MAP["openrouter"] == Config.OPENROUTER_MODEL
    assert _PROVIDER_MODEL_MAP["opencode"] == Config.OPENCODE_MODEL
    assert _PROVIDER_MODEL_MAP["deepseek"] == Config.DEEPSEEK_MODEL
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
    assert cfg["reasoning"]["deepseek_thinking"] == Config.DEEPSEEK_THINKING
    assert cfg["reasoning"]["deepseek_reasoning_effort"] == Config.DEEPSEEK_REASONING_EFFORT


def test_save_experiment_config_unknown_provider_exits(tmp_path):
    args = types.SimpleNamespace(provider="not-a-provider")
    with pytest.raises(SystemExit) as exc:
        _save_experiment_config(
            str(tmp_path),
            args,
            issue_count=1,
            strategy_names=[],
            experiment_id="EXP-x",
        )
    assert exc.value.code == 1
