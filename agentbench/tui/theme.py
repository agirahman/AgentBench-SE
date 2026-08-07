"""Design system for the AgentBench TUI (dark theme).

Palette and layout follow the PRD §5.1 design system. Centralising colours
here keeps every screen consistent and makes theming a single edit.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TuiPalette:
    bg: str = "#0d1117"
    bg_panel: str = "#161b22"
    border: str = "#30363d"
    accent: str = "#58a6ff"
    success: str = "#3fb950"
    warning: str = "#d29922"
    error: str = "#f85149"
    text: str = "#c9d1d9"
    text_dim: str = "#8b949e"
    highlight: str = "#ffd700"


PALETTE = TuiPalette()

# Semantic aliases used by widgets (grey via names, not hex litter).
STATUS_COLORS = {
    "queued": PALETTE.text_dim,
    "running": PALETTE.accent,
    "success": PALETTE.success,
    "fail": PALETTE.error,
}

# Fixed shell layout (from SDD §5.2).
SIDEBAR_WIDTH = 20


def status_color(status: str) -> str:
    """Map a task status string to its palette colour (fallback: text_dim)."""
    return STATUS_COLORS.get(status.lower(), PALETTE.text_dim)