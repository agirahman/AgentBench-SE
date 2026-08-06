"""``pricing`` command: manage model pricing (config override) + show sources.

2-layer model pricing:
  - config override (user-set) wins
  - OpenRouter catalog (dynamic, cached) fills in the rest
  - unknown models => cost $0 with a visible warning

Commands:
  pricing set <model> <input_per_million> <output_per_million>  set override
  pricing remove <model>                                        remove override
  pricing show [model]                                          show resolved pricing
  pricing refresh                                              clear OpenRouter cache
"""

from __future__ import annotations

from rich.table import Table

from agentbench.commands.base import BaseCommand


class PricingCommand(BaseCommand):
    """Handle ``pricing <set|remove|show|refresh>``."""

    def execute(self, args: str) -> None:
        parts = args.split()
        sub = parts[0] if parts else "show"

        if sub == "set":
            self._set(parts[1:])
        elif sub == "remove":
            self._remove(parts[1:])
        elif sub == "refresh":
            self._refresh()
        elif sub == "show":
            self._show(parts[1:])
        else:
            self.error(
                f"Unknown subcommand: '{sub}'. "
                "Use: set|remove|show|refresh"
            )

    # ------------------------------------------------------------------ #
    def _load_override(self) -> dict:
        from agentbench.config_manager import ConfigManager

        cm = ConfigManager()
        cfg = cm.load() if cm.config_exists() else {}
        return cfg.get("pricing", {}) or {}

    def _save_override(self, pricing: dict) -> None:
        from agentbench.config_manager import ConfigManager

        cm = ConfigManager()
        cfg = cm.load() if cm.config_exists() else {}
        cfg = {**cfg, "pricing": pricing}
        cm.save(cfg)

    def _set(self, parts: list[str]) -> None:
        if len(parts) != 3:
            self.error("Usage: pricing set <model> <input_per_million> <output_per_million>")
            return
        model, in_pm, out_pm = parts
        try:
            in_v, out_v = float(in_pm), float(out_pm)
        except ValueError:
            self.error("Prices must be numbers (USD per 1M tokens).")
            return
        pricing = self._load_override()
        pricing[model] = {
            "input_per_million": in_v,
            "output_per_million": out_v,
            "currency": "USD",
            "pricing_source": "manual",
            "pricing_version": "manual",
        }
        self._save_override(pricing)
        self.success(f"Pricing set for '{model}': ${in_v:.4f} / ${out_v:.4f} per 1M (override).")

    def _remove(self, parts: list[str]) -> None:
        if len(parts) != 1:
            self.error("Usage: pricing remove <model>")
            return
        model = parts[0]
        pricing = self._load_override()
        if model not in pricing:
            self.warning(f"No override for '{model}'. (OpenRouter may still price it.)")
            return
        del pricing[model]
        self._save_override(pricing)
        self.success(f"Override removed for '{model}'.")

    def _refresh(self) -> None:
        from agentbench.core.evaluation.pricing_source import clear_openrouter_cache

        clear_openrouter_cache()
        self.success("OpenRouter pricing cache cleared. Next lookup refetches live.")

    def _show(self, params: list[str]) -> None:
        from agentbench.core.evaluation.cost import PricingTable

        def resolve(model: str) -> tuple[dict | None, str]:
            overrides = PricingTable._config_overrides()
            if model in overrides:
                return overrides[model], "override"
            from agentbench.core.evaluation.pricing_source import _openrouter_lookup

            p = _openrouter_lookup(model)
            return (p, "openrouter") if p else (None, "unknown")

        # Resolve models: specific model(s) given, else all config + primary provider
        models = params or self._default_models()

        table = Table(title="Model Pricing", box=None)
        table.add_column("Model", style="cyan")
        table.add_column("Source")
        table.add_column("In /1M", justify="right")
        table.add_column("Out /1M", justify="right")
        for model in models:
            price, source = resolve(model)
            if price:
                table.add_row(
                    model,
                    source,
                    f"${price.get('input_per_million', 0.0):.4f}",
                    f"${price.get('output_per_million', 0.0):.4f}",
                )
            else:
                table.add_row(model, "unknown", "$0.0000 (warn)", "$0.0000 (warn)")
        self.console.print(table)

    def _default_models(self) -> list[str]:
        provider = self.config.get("provider", {}) or {}
        model = provider.get("model", "")
        return [model] if model else []