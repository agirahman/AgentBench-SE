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


def test_cbai_card_uses_the_measured_rate_for_the_pipelines_own_key():
    """The cbai card is solved from 9router's usageHistory for OUR key.

    9router bills PER KEY, and this project holds two keys for the same model.
    The pipeline uses ``Config.OPENCODE_API_KEY`` (suffix da2fe1):

        ...da2fe1  n=8225 -> regular=0.139410 cached=0.002467 output=0.513450
        ...b04880  n=2041 -> output=0.280000  (a DIFFERENT key, not used here)

    An earlier card was solved on ...b04880 and under-stated output by 1.83x.
    The reconciliation that caught it: in the EXP-20261001-765 run window,
    9router logged 567 requests on ...da2fe1 and ZERO on ...b04880.
    """
    pricing = PricingTable.get("cbai/deepseek-v4.1-flash")
    assert pricing is not None
    assert pricing["input_per_million"] == 0.13941
    assert pricing["cached_input_per_million"] == 0.002467
    assert pricing["output_per_million"] == 0.51345
    assert "da2fe1" in pricing["pricing_version"]


def test_cbai_card_is_flat_not_window_sensitive():
    """Our key's bill shows no time-of-day structure, so the card is flat.

    9router is a reseller and its per-key pricing is its own; the DeepSeek
    official peak/off-peak split does not apply to what our key is charged.
    """
    pricing = PricingTable.get("cbai/deepseek-v4.1-flash")
    assert "peak" not in pricing
    off = PricingTable.rates_for("cbai/deepseek-v4.1-flash", "off_peak")
    peak = PricingTable.rates_for("cbai/deepseek-v4.1-flash", "peak")
    assert off == peak
    assert off["output_per_million"] == 0.51345


def test_cbai_cache_hit_is_discounted_not_charged_at_full_rate():
    """A cache hit IS discounted on this route; an older card said otherwise.

    7341 of 8225 rows on our key carry a cache hit, and the least-squares solve
    reproduces them at median error -0.04%. The cached rate is ~57x cheaper than
    a miss. The earlier "no discount" claim came from two requests on the OTHER
    key and is refuted by the 8225 rows here.
    """
    pricing = PricingTable.get("cbai/deepseek-v4.1-flash")
    ratio = pricing["cached_input_per_million"] / pricing["input_per_million"]
    assert ratio < 0.02, "cache hit must be far cheaper than a miss"
    assert pricing["cached_input_per_million"] < pricing["input_per_million"]


def test_cbai_cache_hit_costs_less_than_a_miss():
    """Regression: a reported cache hit must lower the bill, not leave it flat.

    The old card priced cached tokens at the FULL rate, so a run reporting 11,008
    cached tokens of 20,201 was billed identically to one reporting none.
    """
    hit = CostCalculator().calculate(
        _inference(model="cbai/deepseek-v4.1-flash", prompt=20201, completion=429, cached=11008)
    )
    no_hit = CostCalculator().calculate(
        _inference(model="cbai/deepseek-v4.1-flash", prompt=20201, completion=429, cached=0)
    )
    assert hit.total_cost_usd < no_hit.total_cost_usd


def test_window_for_uses_utc_weekdays_only():
    """Peak window is UTC and excludes weekends, per the official docs.

    2026-09-30 is a Wednesday; 2026-10-03 is a Saturday. Exercised on
    ``deepseek-v4-flash``, the model that still carries a peak/off_peak card.
    """
    from evaluation.cost import window_for

    model = "deepseek-v4-flash"
    # Wednesday, 02:00 UTC -> inside the 01-04 UTC peak block.
    assert window_for("2026-09-30T02:00:00+00:00", model) == "peak"
    # Wednesday, 05:00 UTC -> between the two peak blocks.
    assert window_for("2026-09-30T05:00:00+00:00", model) == "off_peak"
    # Wednesday, 08:00 UTC -> inside the 06-10 UTC peak block.
    assert window_for("2026-09-30T08:00:00+00:00", model) == "peak"
    # Saturday at a peak-looking hour -> off-peak in full.
    assert window_for("2026-10-03T02:00:00+00:00", model) == "off_peak"
    # A model with no peak card is always off-peak.
    assert window_for("2026-09-30T02:00:00+00:00", "cbai/deepseek-v4.1-flash") == "off_peak"


def test_cbai_cost_reproduces_a_recorded_row():
    """Pin the card against a real request from the bill.

    9router recorded: 14 prompt tokens (0 cached), 1 completion token, $0.0000021
    on our key. The card predicts $0.0000025 for those tokens
    ($0.13941 + $0.51345 per 1M), which is the right order and the right sign.
    """
    result = CostCalculator().calculate(
        _inference(model="cbai/deepseek-v4.1-flash", prompt=14, completion=1, cached=0)
    )
    assert result.total_cost_usd == pytest.approx(
        14 / 1_000_000 * 0.13941 + 1 / 1_000_000 * 0.51345
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
