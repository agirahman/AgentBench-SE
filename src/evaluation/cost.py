from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from models.inference import InferenceResult
from config import Config

if TYPE_CHECKING:
    from models.result import CostSummary

# DeepSeek peak window, per official pricing docs:
#   https://api-docs.deepseek.com/quick_start/pricing
#   peak = 01:00-04:00 UTC and 06:00-10:00 UTC, Monday-Friday
#   off_peak = everything else, INCLUDING weekends in full and Chinese public
#              holidays in full
# The docs state these hours in UTC. Any pre-2026-10 card that expressed them in
# WIB (UTC+7) was wrong twice over: it shifted the window by 7 hours AND dropped
# the weekend/holiday exclusion, so it billed Sat/Sun requests at peak.
_PEAK_RANGES_UTC_HOUR = [(1, 4), (6, 10)]  # inclusive start, exclusive end


def window_for(timestamp_utc: str, model: str = "") -> str:
    """Return ``"peak"`` or ``"off_peak"`` for a UTC timestamp.

    Official DeepSeek peak hours are 01:00-04:00 and 06:00-10:00 UTC on
    weekdays only; weekends and Chinese public holidays are off-peak in full.

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
    else:
        dt = dt.astimezone(timezone.utc)
    # Weekends are off-peak in full. Python weekday(): Mon=0 .. Sun=6.
    if dt.weekday() >= 5:
        return "off_peak"
    for start, end in _PEAK_RANGES_UTC_HOUR:
        if start <= dt.hour < end:
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
        # 9router (OpenCode route) free testing model. Genuinely $0, not a dummy
        # rate: it is only used to validate the pipeline before the final
        # DeepSeek run, so a zero cost card is accurate.
        "oc/space-bunny-free": {
            "input_per_million": 0.0,
            "output_per_million": 0.0,
            "currency": "USD",
            "pricing_version": "2026-09-free",
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
        # cbai route on 9router, served by DeepSeek-V4.1-Flash (the 9router
        # upstream is codebuddy-intl, which fronts DeepSeek's own API).
        #
        # These rates are MEASURED from 9router's own usageHistory, for the API key
        # this pipeline ACTUALLY uses. That last part is the whole story: 9router
        # bills PER KEY, and this project holds two keys for the same model.
        #
        #   ...da2fe1  = Config.OPENCODE_API_KEY  <-- THE PIPELINE'S KEY
        #                n=8225 rows -> regular=0.139410 cached=0.002467
        #                output=0.513450, median error -0.0407%
        #   ...b04880  = a DIFFERENT key, held in the shell but NOT used by the
        #                pipeline: n=2041 rows -> output=0.280000
        #
        # An earlier version of this card was solved on ...b04880 and therefore
        # under-stated output by 1.83x ($0.28 vs the $0.5135 we are actually
        # charged). The reconciliation that exposed it: in the EXP-20261001-765
        # run window, 9router recorded 567 requests on ...da2fe1 and ZERO on
        # ...b04880. Always confirm the suffix of Config.OPENCODE_API_KEY before
        # re-solving this card.
        #
        # Rates are flat (no peak/off_peak split): the bill for our key shows no
        # time-of-day structure, and 9router is a reseller whose per-key pricing
        # is its own. This is also why the official DeepSeek card is NOT used for
        # reported cost -- our key pays $0.5135/M output, not DeepSeek's $0.60,
        # and paying a reseller's rate is what the thesis must report.
        #
        # Proof the cached rate is a real discount (~57x): 7341 of 8225 rows carry
        # a cache hit, and the solve reproduces them at median error -0.04%. An
        # earlier card set cached == full rate on the claim that no discount was
        # granted; that claim came from two requests on the OTHER key and is
        # refuted by the 8225 rows here.
        #
        # tools/read_actual_bill.py remains the authority: if it disagrees with
        # this card, the bill wins.
        "cbai/deepseek-v4.1-flash": {
            "input_per_million": 0.13941,
            "cached_input_per_million": 0.002467,
            "output_per_million": 0.51345,
            "currency": "USD",
            "pricing_version": "2026-10-01-measured-9router-usageHistory-key-da2fe1",
        },
        # Guard card for the runaway cost cap. It is the SAME numbers as the
        # reported card above, and kept as a separate entry on purpose: the two
        # answer different questions ("what do we report?" vs "what is this act
        # about to spend?"), and on some routes they genuinely diverge. Keeping
        # the seam means calibrating one can never silently move the other.
        #
        # Here they coincide because the reported card is already the measured
        # billed rate. See _cost_so_far() for why the split exists at all.
        "cbai/deepseek-v4.1-flash-billed": {
            "input_per_million": 0.13941,
            "cached_input_per_million": 0.002467,
            "output_per_million": 0.51345,
            "currency": "USD",
            "pricing_version": "2026-10-01-measured-9router-usageHistory-key-da2fe1",
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
        """Return the rate card for *model*, honouring PRICING_MODEL_OVERRIDE.

        The override exists because the budget-curve runs use a deliberately free
        testing model (oc/space-bunny-free) whose genuine rate is $0. Without it
        every cost column reads 0.00, a dollar cost cap can never bind, and RQ3
        has no data at all -- which is exactly what happened to EXP-20260928-003.

        Set PRICING_MODEL_OVERRIDE=deepseek-v4-flash to price those tokens with
        the paid card this project reports on. That produces an ESTIMATE, not a
        measurement, and `aggregate()` marks the version string so the CSV never
        presents it as a real charge.
        """
        return PricingTable.PRICING.get(Config.PRICING_MODEL_OVERRIDE or model)

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
        # inference actually ran in (official DeepSeek peak = 01-04 & 06-10 UTC,
        # weekdays only).
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
        semantic_hit = False
        semantic_saved = 0.0
        semantic_turns = 0
        version = ""
        for inf in inferences:
            c = self.calculate(inf)
            # OR/SUM across the run's inferences: one cached response anywhere is
            # enough to disqualify the whole run from strategy comparison, so this
            # must never be averaged away.
            inf_usage = inf.usage or {}
            if inf_usage.get("semantic_cache_hit"):
                semantic_hit = True
                semantic_saved += float(
                    inf_usage.get("semantic_cache_cost_saved_usd") or 0.0
                )
            semantic_turns += int(inf_usage.get("semantic_cache_hit_turns") or 0)
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
        # When an override priced this run, say so in the artefact. Otherwise the
        # CSV would show a non-zero cost for a run served by a free model, and a
        # reader would take an estimate for a real charge.
        if Config.PRICING_MODEL_OVERRIDE:
            version = f"{version}+priced-as-{Config.PRICING_MODEL_OVERRIDE}"
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
            semantic_cache_hit=semantic_hit,
            semantic_cache_cost_saved_usd=semantic_saved,
            semantic_cache_hit_turns=semantic_turns,
        )
