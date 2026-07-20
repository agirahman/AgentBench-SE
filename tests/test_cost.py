import pytest

from config import Config
from models.inference import InferenceResult
from evaluation.cost import CostCalculator, CostResult, PricingTable


def _inference(model="tencent/hy3", prompt=1000, completion=500):
    return InferenceResult(
        role="executor",
        response="patch",
        usage={
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
        model=model,
    )


def test_pricing_table_hit():
    pricing = PricingTable.get("tencent/hy3")
    assert pricing is not None
    assert pricing["input_per_million"] == 0.14
    assert pricing["output_per_million"] == 0.58
    assert pricing["pricing_version"] == "2026-07"


def test_pricing_table_miss_returns_none():
    pricing = PricingTable.get("unknown-model-xyz")
    assert pricing is None


def test_calculator_accuracy():
    result = CostCalculator().calculate(_inference(prompt=1000, completion=500))
    assert result.input_cost_usd == pytest.approx(1000 / 1_000_000 * 0.14)
    assert result.output_cost_usd == pytest.approx(500 / 1_000_000 * 0.58)
    assert result.total_cost_usd == pytest.approx(
        result.input_cost_usd + result.output_cost_usd
    )


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
