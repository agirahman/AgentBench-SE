"""ASCII welcome banner for the AgentBench TUI (pyfiglet)."""

from __future__ import annotations

import pyfiglet


def ascii_banner(text: str = "AgentBench", font: str = "slant") -> str:
    """Render a large ASCII banner, gracefully falling back on error."""
    try:
        return pyfiglet.figlet_format(text, font=font).rstrip("\n")
    except Exception:  # noqa: BLE001 - font missing should not break startup
        return "AgentBench"


def welcome_banner(config: dict | None = None) -> str:
    """Compose the full welcome block (ASCII art + summary lines)."""
    config = config or {}
    provider = config.get("provider", {}) or {}
    researcher = config.get("researcher", {}) or {}
    lines = [
        ascii_banner(),
        "",
        f"[bold cyan]  Model:      [/bold cyan]{provider.get('model', 'not set')}",
        f"[bold cyan]  Provider:   [/bold cyan]{provider.get('name', 'not set')}",
        f"[bold cyan]  Researcher: [/bold cyan]{researcher.get('name', 'guest')}",
        f"[bold cyan]  Start:      [/bold cyan]/help for commands, /run to launch",
    ]
    return "\n".join(lines)