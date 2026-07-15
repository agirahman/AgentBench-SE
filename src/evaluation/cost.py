from dataclasses import dataclass
from typing import Optional

from models.inference import InferenceResult
from config import Config


@dataclass
class CostResult:
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float
    total_cost_idr: float


class PricingTable:
    PRICING = {
        "gemini-3-flash-preview": {
            "input_per_million": 0.25,
            "output_per_million": 1.50,
            "currency": "USD",
            "pricing_version": "2026-07",
        }
    }

    @staticmethod
    def get(model: str) -> Optional[dict]:
        if model in PricingTable.PRICING:
            return PricingTable.PRICING[model]
        if Config.GEMINI_MODEL in PricingTable.PRICING:
            return PricingTable.PRICING[Config.GEMINI_MODEL]
        return None

    @staticmethod
    def get_rates(model: str) -> tuple[float, float]:
        pricing = PricingTable.get(model)
        if pricing is None:
            return 0.0, 0.0
        return pricing.get("input_per_million", 0.0), pricing.get("output_per_million", 0.0)


class CostCalculator:
    def calculate(self, inference: InferenceResult) -> CostResult:
        input_rate, output_rate = PricingTable.get_rates(inference.model)

        input_cost_usd = (inference.prompt_tokens / 1_000_000) * input_rate
        output_cost_usd = (inference.completion_tokens / 1_000_000) * output_rate
        total_cost_usd = input_cost_usd + output_cost_usd
        total_cost_idr = total_cost_usd * Config.USD_IDR_RATE

        return CostResult(
            model=inference.model,
            input_tokens=inference.prompt_tokens,
            output_tokens=inference.completion_tokens,
            total_tokens=inference.total_tokens,
            input_cost_usd=input_cost_usd,
            output_cost_usd=output_cost_usd,
            total_cost_usd=total_cost_usd,
            total_cost_idr=total_cost_idr,
        )
