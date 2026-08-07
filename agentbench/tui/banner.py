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
        f"  Model       {provider.get('model', 'not set')}",
        f"  Provider    {provider.get('name', 'not set')}",
        f"  Researcher  {researcher.get('name', 'guest')}",
        f"  Start       type 'help' for commands, or 'run --issues N'",
    ]
    return "\n".join(lines)