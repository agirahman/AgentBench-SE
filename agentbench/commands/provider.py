"""``provider`` command: show provider info or run a connection health check."""

from __future__ import annotations

from rich.panel import Panel

from agentbench.commands.base import BaseCommand


def mask_key(key: str) -> str:
    if not key:
        return "(not set)"
    return key[:8] + "***"


class ProviderCommand(BaseCommand):
    """Handle ``provider [--test]``."""

    def execute(self, args: str) -> None:
        provider_cfg = self.config.get("provider", {}) or {}
        name = provider_cfg.get("name", "unknown")
        model = provider_cfg.get("model", "unknown")
        api_key = provider_cfg.get("api_key", "")

        if "--test" in args:
            self._run_test(name, api_key, model)
            return

        self.console.print(Panel.fit(
            f"[bold]Provider:[/bold] {name}\n"
            f"[bold]Model:[/bold] {model}\n"
            f"[bold]API Key:[/bold] {mask_key(api_key)}\n"
            f"[bold]Status:[/bold] configured — run 'provider --test' to verify",
            title="Provider Info",
            border_style="cyan",
        ))

    def _run_test(self, name: str, api_key: str, model: str) -> None:
        self.info(f"Testing provider '{name}' ({model})...")
        from agentbench.provider_health import check_connection

        ok, message = check_connection(name, api_key, model)
        if ok:
            self.success(f"✓ Provider online. {message}")
        else:
            self.error(f"✗ Provider error: {message}")