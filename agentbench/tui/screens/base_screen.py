"""Base screen for the AgentBench TUI.

All screens share the footer hint contract: each screen declares a
``footer_hint`` string that the main app shows in the shell footer, and a
``nav_label`` for the sidebar. Screen bodies are simple placeholders in
Patch 1; concrete functionality lands in later patches.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static


class BaseScreen(Screen):
    """Placeholder screen with a nav title and footer hint."""

    nav_label: str = "Screen"
    footer_hint: str = ""

    def compose(self) -> ComposeResult:
        yield Static(
            f"[bold accent]{self.nav_label}[/bold accent]\n\n"
            "[dim]Coming in a later patch.[/dim]",
            id="screen-placeholder",
        )