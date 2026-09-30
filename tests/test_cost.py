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


def test_cbai_card_is_the_measured_one_not_the_public_deepseek_card():
    """The cbai route is a reseller with its own pricing.

    Solved from 9router's usageHistory. DeepSeek's public off-peak card overstates
    this route's spend by 71% and the peak card by 243%, so a substitution here
    would inflate every RQ3 dollar figure.
    """
    pricing = PricingTable.get("cbai/deepseek-v4.1-flash")
    assert pricing is not None
    assert pricing["input_per_million"] == 0.14
    assert pricing["output_per_million"] == 0.28
    # Guard against someone pasting the DeepSeek card in later.
    assert pricing["input_per_million"] != 0.22
    assert pricing["output_per_million"] != 0.66
    assert "measured" in pricing["pricing_version"]


def test_cbai_cached_rate_equals_full_rate_because_no_discount_was_granted():
    """A reported cache hit was still charged at full price on this route.

    Measured: two requests reported real hits (256 and 896 cached tokens via
    prompt_tokens_details.cached_tokens) and 9router charged charged/full-price =
    1.0000 for both. Applying the historical cached rate ($0.002833/M, 49x
    cheaper) made our accounting read $0.001438 for a run the bill charged
    $0.002948 -- a 2.05x under-report, in the direction that flatters a cost claim.

    If this ever changes, the fix is to re-derive the card from the bill, not to
    simply relax this assertion.
    """
    pricing = PricingTable.get("cbai/deepseek-v4.1-flash")
    assert pricing["cached_input_per_million"] == pricing["input_per_million"], (
        "the cached rate must not undercut the full rate while the route charges "
        "full price for cache hits"
    )
    assert "no-cache-discount" in pricing["pricing_version"]


def test_cbai_cost_reproduces_the_smoke_run_bill():
    """Pin the card against the bill for a real run, token by token.

    EXP-20260930-022's five tool-loop requests were charged $0.002948 by 9router
    and reported 20,201 prompt + 429 completion tokens with NO cache discount.
    A card that cannot reproduce a known bill is not usable for RQ3.
    """
    result = CostCalculator().calculate(
        _inference(model="cbai/deepseek-v4.1-flash", prompt=20201, completion=429, cached=0)
    )
    assert result.total_cost_usd == pytest.approx(0.002948, abs=1e-6)


def test_cbai_cost_does_not_halve_when_a_cache_hit_is_reported():
    """Regression: a cache hit must not silently cut the bill in half.

    Before the fix, 11,008 tokens reported as cached were priced at $0.002833/M,
    producing $0.001438 against a real charge of $0.002948. This pins the ratio.
    """
    hit = CostCalculator().calculate(
        _inference(model="cbai/deepseek-v4.1-flash", prompt=20201, completion=429, cached=11008)
    )
    no_hit = CostCalculator().calculate(
        _inference(model="cbai/deepseek-v4.1-flash", prompt=20201, completion=429, cached=0)
    )
    assert hit.total_cost_usd == pytest.approx(no_hit.total_cost_usd)


def test_cbai_card_has_no_peak_window():
    """Flat card: the route does not bill by DeepSeek's WIB peak windows.

    A peak/off-peak card would make cost depend on the wall-clock hour, and the
    recorded rows show no such split (blended $/1M varies with cache mix, not
    with the hour). Keeping it flat means the same tokens cost the same whenever
    the run happens.
    """
    pricing = PricingTable.get("cbai/deepseek-v4.1-flash")
    assert "peak" not in pricing
    off = PricingTable.rates_for("cbai/deepseek-v4.1-flash", "off_peak")
    peak = PricingTable.rates_for("cbai/deepseek-v4.1-flash", "peak")
    assert off == peak


def test_cbai_cost_reproduces_a_recorded_row():
    """Pin the card against a real request from the bill.

    9router recorded: 14 prompt tokens (0 cached), 1 completion token, $0.0000021.
    A card that cannot reproduce a known line of the bill is not usable for RQ3.
    """
    result = CostCalculator().calculate(
        _inference(model="cbai/deepseek-v4.1-flash", prompt=14, completion=1, cached=0)
    )
    assert result.total_cost_usd == pytest.approx(
        14 / 1_000_000 * 0.14 + 1 / 1_000_000 * 0.28
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
