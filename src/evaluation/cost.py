from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from models.inference import InferenceResult
from config import Config

if TYPE_CHECKING:
    from models.result import CostSummary

# DeepSeek peak window (WIB / UTC+7), per official pricing docs:
#   peak  = 08:00–11:00 WIB  and  13:00–17:00 WIB
#   off_peak = everything else
_WIB_OFFSET = 7  # hours
_PEAK_RANGES_WIB_HOUR = [(8, 11), (13, 17)]  # inclusive start, exclusive end


def window_for(timestamp_utc: str, model: str = "") -> str:
    """Return ``"peak"`` or ``"off_peak"`` for a UTC timestamp, in WIB (UTC+7).

    Only models with a ``peak`` rate card (e.g. deepseek-v4-flash) are
    window-sensitive; everything else is always ``"off_peak"``.
    """
    from evaluation.cost import PricingTable
    pricing = PricingTable.get(model)
    if not pricing or "peak" not in pricing:
        return "off_peak"
    try:
        dt = datetime.fromisoformat(timestamp_utc)
    except (ValueError, TypeError):
        return "off_peak"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    wib_hour = (dt.hour + _WIB_OFFSET) % 24
    for start, end in _PEAK_RANGES_WIB_HOUR:
        if start <= wib_hour < end:
            return "peak"
    return "off_peak"


@dataclass
class CostResult:
    model: str
    input_tokens: int
    cached_input_tokens: int
    regular_input_tokens: int
    output_tokens: int
    total_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float
    total_cost_idr: float
    cached_input_cost_usd: float
    regular_input_cost_usd: float
    peak_total_cost_usd: float
    peak_total_cost_idr: float
    actual_cost_usd: float = 0.0
    actual_cost_idr: float = 0.0
    window: str = "off_peak"


class PricingTable:
    PRICING = {
        "gemini-3-flash-preview": {
            "input_per_million": 0.50,
            "output_per_million": 3.00,
            "currency": "USD",
            "pricing_version": "2026-07",
        },
        # OpenRouter testing model: free tier, so cost metrics are genuinely $0
        # (not a dummy rate). Used only for pipeline validation before the final
        # DeepSeek run, so a $0 cost card is accurate, not a placeholder.
        "stealth/space-bunny-alpha": {
            "input_per_million": 0.0,
            "output_per_million": 0.0,
            "currency": "USD",
            "pricing_version": "2026-09-free",
        },
        # OpenRouter route to the SAME DeepSeek model as the official API. The
        # official DeepSeek rate card is applied so RQ3 is comparable across the
        # two routes; note in the methodology that this is a modelled price.
        "deepseek/deepseek-v4-flash": {
            "off_peak": {
                "input_per_million": 0.22,
                "cached_input_per_million": 0.007,
                "output_per_million": 0.66,
            },
            "peak": {
                "input_per_million": 0.44,
                "cached_input_per_million": 0.014,
                "output_per_million": 1.32,
            },
            "currency": "USD",
            "pricing_version": "2026-08-16-via-openrouter",
        },
        "oc/deepseek-v4-flash-free": {
            "input_per_million": 0.0,
            "output_per_million": 0.0,
            "currency": "USD",
            "pricing_version": "2026-07",
        },
        "deepseek-v4-flash": {
            "off_peak": {
                "input_per_million": 0.22,
                "cached_input_per_million": 0.007,
                "output_per_million": 0.66,
            },
            "peak": {
                "input_per_million": 0.44,
                "cached_input_per_million": 0.014,
                "output_per_million": 1.32,
            },
            "currency": "USD",
            "pricing_version": "2026-08-16",
        },
        # CommandCode-routed models. Same underlying DeepSeek model, different
        # provider route, so the official DeepSeek rate card is applied as a
        # DUMMY pricing (non-zero) so cost metrics are never $0 in reports.
        "cmd/deepseek/deepseek-v4-flash": {
            "off_peak": {
                "input_per_million": 0.22,
                "cached_input_per_million": 0.007,
                "output_per_million": 0.66,
            },
            "peak": {
                "input_per_million": 0.44,
                "cached_input_per_million": 0.014,
                "output_per_million": 1.32,
            },
            "currency": "USD",
            "pricing_version": "2026-08-16",
        },
        "cmd/poolside/laguna-s-2.1-free": {
            "off_peak": {
                "input_per_million": 0.22,
                "cached_input_per_million": 0.007,
                "output_per_million": 0.66,
            },
            "peak": {
                "input_per_million": 0.44,
                "cached_input_per_million": 0.014,
                "output_per_million": 1.32,
            },
            "currency": "USD",
            "pricing_version": "2026-08-16-dummy",
        },
        "tencent/hy3:free": {
            "input_per_million": 0.0,
            "output_per_million": 0.0,
            "currency": "USD",
            "pricing_version": "2026-07",
        },
        "tencent/hy3": {
            "input_per_million": 0.14,
            "output_per_million": 0.58,
            "currency": "USD",
            "pricing_version": "2026-07",
        },
        "llama-3.3-70b-versatile": {
            "input_per_million": 0.59,
            "output_per_million": 0.79,
            "currency": "USD",
            "pricing_version": "2026-07",
        },
        "openai/gpt-oss-120b": {
            "input_per_million": 0.15,
            "output_per_million": 0.60,
            "currency": "USD",
            "pricing_version": "2026-07",
        },
        "poolside/laguna-m.1:free": {
            "input_per_million": 0.0,
            "output_per_million": 0.0,
            "currency": "USD",
            "pricing_version": "2026-07",
        },
        "poolside/laguna-s-2.1:free": {
            "input_per_million": 0.0,
            "output_per_million": 0.0,
            "currency": "USD",
            "pricing_version": "2026-08",
        },
    }

    @staticmethod
    def get(model: str) -> Optional[dict]:
        return PricingTable.PRICING.get(model)

    @staticmethod
    def rates_for(model: str, window: str = "off_peak") -> dict:
        """Return rate card dict (input/cached_input/output per 1M) for a window.

        Flat (legacy) entries apply the same rates to every window.
        """
        pricing = PricingTable.get(model)
        if pricing is None:
            return {"input_per_million": 0.0, "cached_input_per_million": 0.0, "output_per_million": 0.0}
        if "off_peak" in pricing:
            card = dict(pricing.get(window, pricing.get("off_peak", {})))
        else:
            card = dict(pricing)
        card.setdefault("cached_input_per_million", card.get("input_per_million", 0.0))
        return card

    @staticmethod
    def get_rates(model: str) -> tuple[float, float]:
        card = PricingTable.rates_for(model, "off_peak")
        return card.get("input_per_million", 0.0), card.get("output_per_million", 0.0)


class CostCalculator:
    def calculate(self, inference: InferenceResult) -> CostResult:
        off = PricingTable.rates_for(inference.model, "off_peak")
        peak = PricingTable.rates_for(inference.model, "peak")

        cached = inference.cached_tokens
        regular = inference.regular_input_tokens
        prompt_t = inference.prompt_tokens
        comp_t = inference.completion_tokens

        input_cost_usd = (regular / 1_000_000) * off["input_per_million"] + (cached / 1_000_000) * off["cached_input_per_million"]
        output_cost_usd = (comp_t / 1_000_000) * off["output_per_million"]
        total_cost_usd = input_cost_usd + output_cost_usd
        total_cost_idr = total_cost_usd * Config.USD_IDR_RATE

        peak_input_usd = (regular / 1_000_000) * peak["input_per_million"] + (cached / 1_000_000) * peak["cached_input_per_million"]
        peak_output_usd = (comp_t / 1_000_000) * peak["output_per_million"]
        peak_total_usd = peak_input_usd + peak_output_usd
        peak_total_idr = peak_total_usd * Config.USD_IDR_RATE

        # Window-aware actual cost: use the rate card for the window the
        # inference actually ran in (WIB peak 08-11 & 13-17, per docs).
        window = window_for(inference.timestamp, inference.model)
        actual_card = PricingTable.rates_for(inference.model, window)
        actual_input_usd = (regular / 1_000_000) * actual_card["input_per_million"] + (cached / 1_000_000) * actual_card["cached_input_per_million"]
        actual_output_usd = (comp_t / 1_000_000) * actual_card["output_per_million"]
        actual_total_usd = actual_input_usd + actual_output_usd
        actual_total_idr = actual_total_usd * Config.USD_IDR_RATE

        return CostResult(
            model=inference.model,
            input_tokens=prompt_t,
            cached_input_tokens=cached,
            regular_input_tokens=regular,
            output_tokens=comp_t,
            total_tokens=inference.total_tokens,
            input_cost_usd=input_cost_usd,
            output_cost_usd=output_cost_usd,
            total_cost_usd=total_cost_usd,
            total_cost_idr=total_cost_idr,
            cached_input_cost_usd=(cached / 1_000_000) * off["cached_input_per_million"],
            regular_input_cost_usd=(regular / 1_000_000) * off["input_per_million"],
            peak_total_cost_usd=peak_total_usd,
            peak_total_cost_idr=peak_total_idr,
            actual_cost_usd=actual_total_usd,
            actual_cost_idr=actual_total_idr,
            window=window,
        )

    def aggregate(self, inferences: list[InferenceResult]) -> "CostSummary":
        """Sum cost across all inferences into a CostSummary."""
        from models.result import CostSummary

        input_usd = output_usd = total_usd = total_idr = 0.0
        cached_tok = regular_tok = 0
        cached_usd = regular_usd = peak_usd = peak_idr = 0.0
        actual_usd = actual_idr = 0.0
        version = ""
        for inf in inferences:
            c = self.calculate(inf)
            input_usd += c.input_cost_usd
            output_usd += c.output_cost_usd
            total_usd += c.total_cost_usd
            total_idr += c.total_cost_idr
            cached_tok += c.cached_input_tokens
            regular_tok += c.regular_input_tokens
            cached_usd += c.cached_input_cost_usd
            regular_usd += c.regular_input_cost_usd
            peak_usd += c.peak_total_cost_usd
            peak_idr += c.peak_total_cost_idr
            actual_usd += c.actual_cost_usd
            actual_idr += c.actual_cost_idr
            pricing = PricingTable.get(inf.model)
            if pricing:
                version = pricing.get("pricing_version", "")
        return CostSummary(
            input_cost_usd=input_usd,
            output_cost_usd=output_usd,
            total_cost_usd=total_usd,
            total_cost_idr=total_idr,
            pricing_version=version,
            cached_input_tokens=cached_tok,
            regular_input_tokens=regular_tok,
            cached_input_cost_usd=cached_usd,
            regular_input_cost_usd=regular_usd,
            peak_total_cost_usd=peak_usd,
            peak_total_cost_idr=peak_idr,
            actual_cost_usd=actual_usd,
            actual_cost_idr=actual_idr,
        )
