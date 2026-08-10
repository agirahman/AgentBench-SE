"""Action sheet modal (Patch 8): all shortcuts of the active screen.

Pressed with ``?`` (binding on the ShellScreen base, inherited by every
screen). Shows global bindings plus the current screen's bindings in a
scrollable modal — the "action sheet semua shortcut" acceptance
criterion (PRD Patch 8).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from agentbench.tui import shortcuts


def _rows(bindings: list[tuple[str, str]]) -> list[str]:
    return [
        f"[b accent]{key:<12}[/b accent] [dim]{desc}[/dim]"
        for key, desc in bindings
    ]


class ActionSheet(ModalScreen[None]):
    """Modal listing global + active-screen keyboard shortcuts."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
    ]

    def __init__(self, screen_key: str, screen_label: str) -> None:
        super().__init__()
        self._screen_key = screen_key
        self._screen_label = screen_label

    def compose(self) -> ComposeResult:
        from agentbench.tui.app import AgentBenchTUI

        screen_bindings = _rows(
            shortcuts.screen_bindings().get(self._screen_key, [])
        )
        with VerticalScroll(id="as-wrap"):
            yield Static(
                f"[b accent]Shortcuts — {self._screen_label}[/b accent]\n",
                id="as-title",
            )
            yield Static(
                _section("Global", _rows(shortcuts.GLOBAL_SHORTCUTS))
                + "\n\n"
                + _section(self._screen_label, screen_bindings),
                id="as-body",
            )
            yield Static(
                "[dim]Esc / Q — tutup[/dim]",
                id="as-hint",
            )

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.dismiss(None)


def _section(title: str, rows: list[str]) -> str:
    body = "\n".join(rows) if rows else "[dim]— none —[/dim]"
    return f"[b]{title}[/b]\n{body}"
