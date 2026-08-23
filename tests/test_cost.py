import pytest

from config import Config
from models.inference import InferenceResult
from evaluation.cost import CostCalculator, CostResult, PricingTable


def _inference(model="tencent/hy3", prompt=1000, completion=500, cached=0):
    return InferenceResult(
        role="executor",
        response="patch",
        usage={
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "cached_tokens": cached,
        },
        model=model,
    )


def test_pricing_table_hit():
    pricing = PricingTable.get("tencent/hy3")
    assert pricing is not None
    assert pricing["input_per_million"] == 0.14
    assert pricing["output_per_million"] == 0.58
    assert pricing["pricing_version"] == "2026-07"


def test_deepseek_pricing_off_peak_structure():
    pricing = PricingTable.get("deepseek-v4-flash")
    assert pricing is not None
    off = pricing["off_peak"]
    peak = pricing["peak"]
    assert off["input_per_million"] == 0.22
    assert off["cached_input_per_million"] == 0.007
    assert off["output_per_million"] == 0.66
    assert peak["input_per_million"] == 0.44
    assert peak["cached_input_per_million"] == 0.014
    assert peak["output_per_million"] == 1.32
    assert pricing["pricing_version"] == "2026-08-16"


def test_rates_for_flat_model_uses_same_card():
    off = PricingTable.rates_for("tencent/hy3", "off_peak")
    peak = PricingTable.rates_for("tencent/hy3", "peak")
    assert off["input_per_million"] == peak["input_per_million"] == 0.14
    assert off["cached_input_per_million"] == 0.14


def test_calculator_cached_input_breakdown():
    result = CostCalculator().calculate(
        _inference(model="deepseek-v4-flash", prompt=1000, completion=500, cached=700)
    )
    assert result.cached_input_tokens == 700
    assert result.regular_input_tokens == 300
    assert result.regular_input_cost_usd == pytest.approx(300 / 1_000_000 * 0.22)
    assert result.cached_input_cost_usd == pytest.approx(700 / 1_000_000 * 0.007)
    assert result.input_cost_usd == pytest.approx(
        result.regular_input_cost_usd + result.cached_input_cost_usd
    )


def test_calculator_peak_off_peak_both_recorded():
    result = CostCalculator().calculate(
        _inference(model="deepseek-v4-flash", prompt=1000, completion=500, cached=700)
    )
    assert result.total_cost_usd == pytest.approx(
        (300 / 1_000_000 * 0.22) + (700 / 1_000_000 * 0.007) + (500 / 1_000_000 * 0.66)
    )
    assert result.peak_total_cost_usd == pytest.approx(
        (300 / 1_000_000 * 0.44) + (700 / 1_000_000 * 0.014) + (500 / 1_000_000 * 1.32)
    )
    assert result.peak_total_cost_usd > result.total_cost_usd
    assert result.peak_total_cost_idr == pytest.approx(
        result.peak_total_cost_usd * Config.USD_IDR_RATE
    )


def test_calculator_accuracy():
    result = CostCalculator().calculate(_inference(prompt=1000, completion=500))
    assert result.input_cost_usd == pytest.approx(1000 / 1_000_000 * 0.14)
    assert result.output_cost_usd == pytest.approx(500 / 1_000_000 * 0.58)
    assert result.total_cost_usd == pytest.approx(
        result.input_cost_usd + result.output_cost_usd
    )
    assert result.peak_total_cost_usd == pytest.approx(result.total_cost_usd)


def test_pricing_table_miss_returns_none():
    pricing = PricingTable.get("unknown-model-xyz")
    assert pricing is None


def test_cost_idr_conversion():
    result = CostCalculator().calculate(_inference())
    assert result.total_cost_idr == pytest.approx(
        result.total_cost_usd * Config.USD_IDR_RATE
    )


def test_cost_result_breakdown_consistency():
    result = CostCalculator().calculate(_inference())
    assert isinstance(result, CostResult)
    assert result.input_cost_usd + result.output_cost_usd == pytest.approx(
        result.total_cost_usd
    )
    assert result.total_tokens == result.input_tokens + result.output_tokens
