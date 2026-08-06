from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from agentbench.core.models.inference import InferenceResult
from agentbench.core.config import Config

if TYPE_CHECKING:
    from agentbench.core.models.result import CostSummary


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
    """2-layer model pricing resolver.

    Order (first match wins):
      1. config override — user-set ``pricing`` section (config.yaml)
      2. OpenRouter catalog — live prices, cached 6h (free => $0)
      3. None — caller treats cost as $0 with a visible warning
    """

    @staticmethod
    def _config_overrides() -> dict:
        """Return the user ``pricing`` overrides from config.yaml (memoized)."""
        try:
            from agentbench.config_manager import ConfigManager

            cm = ConfigManager()
            if not cm.config_exists():
                return {}
            cfg = cm.load()
            return cfg.get("pricing", {}) or {}
        except Exception:  # noqa: BLE001 - overrides are best-effort
            return {}

    @staticmethod
    def get(model: str) -> Optional[dict]:
        if not model:
            return None

        # 1) config override (explicit user value wins)
        overrides = PricingTable._config_overrides()
        if model in overrides:
            return dict(overrides[model])

        # 2) OpenRouter catalog (dynamic)
        from agentbench.core.evaluation.pricing_source import _openrouter_lookup

        return _openrouter_lookup(model)

    @staticmethod
    def get_rates(model: str) -> tuple[float, float]:
        pricing = PricingTable.get(model)
        if pricing is None:
            return 0.0, 0.0
        return pricing.get("input_per_million", 0.0), pricing.get("output_per_million", 0.0)


class CostCalculator:
    def __init__(self, usd_idr_rate: float | None = None):
        """``usd_idr_rate`` defaults to the live value from config.yaml
        (fallback: Config.USD_IDR_RATE env, then 16500)."""
        self._usd_idr_rate = usd_idr_rate if usd_idr_rate is not None else self._load_rate()

    @staticmethod
    def _load_rate() -> float:
        try:
            from agentbench.config_manager import ConfigManager

            cm = ConfigManager()
            if cm.config_exists():
                rate = cm.load().get("experiment", {}).get("usd_idr_rate")
                if rate:
                    return float(rate)
        except Exception:  # noqa: BLE001 - fall back to env/default
            pass
        return float(Config.USD_IDR_RATE)

    def calculate(self, inference: InferenceResult) -> CostResult:
        pricing = PricingTable.get(inference.model)
        if pricing is None:
            input_rate = output_rate = 0.0
        else:
            input_rate = pricing.get("input_per_million", 0.0)
            output_rate = pricing.get("output_per_million", 0.0)

        input_cost_usd = (inference.prompt_tokens / 1_000_000) * input_rate
        output_cost_usd = (inference.completion_tokens / 1_000_000) * output_rate
        total_cost_usd = input_cost_usd + output_cost_usd
        total_cost_idr = total_cost_usd * self._usd_idr_rate

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

    def aggregate(self, inferences: list[InferenceResult]) -> "CostSummary":
        """Sum cost across all inferences into a CostSummary."""
        from agentbench.core.models.result import CostSummary

        input_usd = output_usd = total_usd = total_idr = 0.0
        version = ""
        for inf in inferences:
            c = self.calculate(inf)
            input_usd += c.input_cost_usd
            output_usd += c.output_cost_usd
            total_usd += c.total_cost_usd
            total_idr += c.total_cost_idr
            pricing = PricingTable.get(inf.model)
            if pricing:
                version = pricing.get("pricing_version", "")
        return CostSummary(
            input_cost_usd=input_usd,
            output_cost_usd=output_usd,
            total_cost_usd=total_usd,
            total_cost_idr=total_idr,
            pricing_version=version,
        )
