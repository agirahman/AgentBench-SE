"""Tests for the 2-layer pricing resolver (config override + OpenRouter)."""

import pytest

from agentbench.core.evaluation import cost as cost_mod
from agentbench.core.evaluation import pricing_source as ps


@pytest.fixture(autouse=True)
def clear_catalog_cache():
    ps.clear_openrouter_cache()
    yield
    ps.clear_openrouter_cache()


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """A config dir with an empty pricing section for override tests."""
    from agentbench.config_manager import ConfigManager

    monkeypatch.setenv("AGENTBENCH_CONFIG_DIR", str(tmp_path))
    cm = ConfigManager(config_path=tmp_path / "config.yaml")
    cm.save({
        "researcher": {"name": "Agi"},
        "provider": {"name": "openrouter", "api_key": "x", "model": "m"},
        "experiment": {"temperature": 0.2, "max_retries": 3,
                       "rate_limit": 1.5, "usd_idr_rate": 17800.0},
        "pricing": {},
    })
    return cm


def test_override_wins_over_openrouter(monkeypatch, tmp_config):
    """Config override (layer 1) beats the OpenRouter catalog (layer 2)."""
    monkeypatch.setattr(
        ps, "get_openrouter_pricing", lambda mid, force_refresh=False: {
            "input_per_million": 0.5, "output_per_million": 1.0,
            "pricing_source": "openrouter",
        }
    )
    # add an override via raw config edit
    cfg = tmp_config.load()
    cfg["pricing"] = {"m": {"input_per_million": 0.05, "output_per_million": 0.1,
                            "pricing_source": "manual"}}
    tmp_config.save(cfg)

    price = cost_mod.PricingTable.get("m")
    assert price is not None
    assert price["pricing_source"] == "manual"
    assert price["input_per_million"] == 0.05


def test_openrouter_used_when_no_override(monkeypatch, tmp_config):
    """Without override, OpenRouter pricing is used."""
    monkeypatch.setattr(
        ps, "get_openrouter_pricing", lambda mid, force_refresh=False: {
            "input_per_million": 0.0882, "output_per_million": 0.1764,
            "pricing_source": "openrouter",
        }
    )
    price = cost_mod.PricingTable.get("m")
    assert price is not None
    assert price["pricing_source"] == "openrouter"
    assert price["input_per_million"] == 0.0882


def test_unknown_model_returns_none(monkeypatch, tmp_config):
    monkeypatch.setattr(
        ps, "get_openrouter_pricing", lambda mid, force_refresh=False: None
    )
    assert cost_mod.PricingTable.get("nope/not-real") is None
    assert cost_mod.PricingTable.get_rates("nope/not-real") == (0.0, 0.0)


def test_get_rates_zero_cost_unknown(monkeypatch, tmp_config):
    monkeypatch.setattr(
        ps, "get_openrouter_pricing", lambda mid, force_refresh=False: None
    )
    assert cost_mod.PricingTable.get_rates("nope") == (0.0, 0.0)


def test_normalize_model_id():
    assert ps.normalize_model_id("oc/deepseek-v4-flash") == "deepseek-v4-flash"
    assert ps.normalize_model_id("oc/deepseek-v4-flash-free") == "deepseek-v4-flash-free"
    assert ps.normalize_model_id("~deepseek/deepseek-v4-flash-latest") == "deepseek/deepseek-v4-flash-latest"
    assert ps.normalize_model_id("deepseek/deepseek-v4-flash") == "deepseek/deepseek-v4-flash"
    assert ps.normalize_model_id("opencode/some-model") == "some-model"


def test_openrouter_lookup_tries_normalized(monkeypatch):
    """When raw id misses, the normalized id is tried."""
    calls = []

    def fake_get(mid, force_refresh=False):
        calls.append(mid)
        # normalized id (deepseek-v4-flash) is in the catalog
        if mid == "deepseek-v4-flash":
            return {"input_per_million": 0.0882, "output_per_million": 0.1764}
        return None

    monkeypatch.setattr(ps, "get_openrouter_pricing", fake_get)
    result = ps._openrouter_lookup("oc/deepseek-v4-flash")
    assert result is not None
    assert calls[0] == "oc/deepseek-v4-flash"   # raw first
    assert calls[1] == "deepseek-v4-flash"      # normalized second


def test_cost_calculator_uses_config_rate(tmp_config):
    """CostCalculator picks up the live rate from config.yaml."""
    calc = cost_mod.CostCalculator()
    assert calc._usd_idr_rate == 17800.0


def test_cost_calculator_explicit_rate_wins(tmp_config):
    calc = cost_mod.CostCalculator(usd_idr_rate=9999.0)
    assert calc._usd_idr_rate == 9999.0