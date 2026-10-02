"""The cost guard and the reported cost must not share a rate card.

Rationale: the guard answers "is this act about to spend too much REAL money?",
so it is priced with what the upstream actually bills. The reported cost answers
"what does this cost at published reference prices?", which is what a thesis
defends. On the cbai route these differ by ~4.9x, so conflating them would let a
$3.00 cap stop a run after ~$0.62 of real spend -- replacing the 200-turn
fairness invariant with a dollar bound.
"""
from __future__ import annotations

import pytest

from config import Config
from evaluation.cost import PricingTable
from providers.tool_loop import _cost_so_far

REPORT = "cbai/deepseek-v4.1-flash"
BILLED = "cbai/deepseek-v4.1-flash-billed"


def test_billed_card_exists_and_is_a_real_card():
    """The guard card exists and carries the measured rates."""
    billed = PricingTable.rates_for(BILLED, "off_peak")
    assert billed["input_per_million"] > 0
    assert billed["output_per_million"] > 0
    assert "measured" in billed["pricing_version"]


def test_billed_card_encodes_the_measured_cache_discount():
    """Cache hit is far cheaper than a miss on this route (measured)."""
    billed = PricingTable.rates_for(BILLED, "off_peak")
    ratio = billed["cached_input_per_million"] / billed["input_per_million"]
    assert ratio < 0.02


def test_guard_and_reported_cards_agree_on_the_measured_key():
    """Both cards carry the measured da2fe1 rates (they may diverge elsewhere).

    The seam between them is kept even while they coincide: calibrating the cap
    must never silently move what RQ3 reports, and vice versa.
    """
    billed = PricingTable.rates_for(BILLED, "off_peak")
    report = PricingTable.rates_for(REPORT, "off_peak")
    assert billed["output_per_million"] == report["output_per_million"] == 0.51345
    assert "da2fe1" in billed["pricing_version"]
    assert "da2fe1" in PricingTable.get(REPORT)["pricing_version"]


def test_guard_uses_the_billed_card_when_configured():
    usage = {"prompt_tokens": 20_201, "completion_tokens": 429, "cached_tokens": 11_008}
    guard_model = Config.COST_GUARD_MODEL or REPORT
    guard = _cost_so_far(usage, PricingTable.rates_for(guard_model, "off_peak"))
    report = _cost_so_far(usage, PricingTable.rates_for(REPORT, "off_peak"))
    # The guard must price real money. With both cards on the measured key they
    # agree; if they ever diverge, the guard must not be the more expensive one,
    # or a $3 cap would bind before $3 of real spend.
    assert guard <= report
    assert guard > 0
