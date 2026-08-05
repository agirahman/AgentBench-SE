"""``config`` command: show / set / reset the persistent configuration."""

from __future__ import annotations

from rich.panel import Panel
from rich.prompt import Confirm

from agentbench.commands.base import BaseCommand
from agentbench.config_manager import ConfigManager, ConfigError


def mask_api_key(api_key: str) -> str:
    """Return a masked representation: first 8 chars + "***"."""
    if not api_key:
        return "(not set)"
    return api_key[:8] + "***"


class ConfigCommand(BaseCommand):
    """Handle ``config <show|set <key> <value>|reset>``."""

    def __init__(self, config: dict, console, config_manager: ConfigManager | None = None):
        super().__init__(config, console)
        self._cm = config_manager or ConfigManager()

    def execute(self, args: str) -> None:
        parts = args.split()
        sub = parts[0] if parts else "show"

        if sub == "show":
            self._show_config()
        elif sub == "set":
            self._set_config(parts[1:])
        elif sub == "reset":
            self._reset_config()
        else:
            self.error(f"Unknown subcommand: '{sub}'. Use: show|set|reset")

    def _show_config(self) -> None:
        try:
            cfg = self._cm.load()
        except Exception as e:  # noqa: BLE001
            self.error(str(e))
            return
        masked = cfg.copy()
        prov = dict(masked.get("provider", {}))
        if "api_key" in prov:
            prov["api_key"] = mask_api_key(prov["api_key"])
        masked["provider"] = prov

        from rich.syntax import Syntax
        import yaml

        text = yaml.safe_dump(masked, default_flow_style=False, sort_keys=False)
        self.console.print(Panel(
            Syntax(text, "yaml", theme="default"),
            title="Current Configuration",
            border_style="cyan",
        ))

    def _set_config(self, args: list[str]) -> None:
        if len(args) != 2:
            self.error("Usage: config set <key> <value>")
            return
        key, value = args
        try:
            updated = self._cm.set_value(key, value)
        except Exception as e:  # noqa: BLE001
            self.error(str(e))
            return
        self.success(f"Updated {key} = {value}")

    def _reset_config(self) -> None:
        if not self._cm.config_exists():
            self.warning("No configuration file to reset.")
            return
        if not Confirm.ask("Delete configuration and re-run setup?", default=False):
            self.info("Aborted.")
            return
        self._cm.reset()
        self.success("Configuration deleted. Re-run 'agentbench setup' when ready.")