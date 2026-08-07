"""Shared shell (header + sidebar nav + content region) for the TUI.

Every screen composes the same shell chrome (header line, left sidebar
with nav buttons, content region, footer hint) so navigation looks
persistent across screens. Sidebar buttons call ``app.nav_to(key)``, which
switches the active screen (PRD §5.2).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Static

NAV_ITEMS: list[tuple[str, str]] = [
    ("setup", "Setup"),
    ("run", "Run"),
    ("results", "Results"),
    ("config", "Config"),
    ("logs", "Logs"),
    ("help", "Help"),
    ("console", "Console"),
]

SIDEBAR_WIDTH = 18


class ShellScreen(Screen):
    """Base screen: persistent-looking shell with a content region.

    Subclasses set ``nav_key`` and override ``body()`` (widgets for the
    content region). ``footer_hint`` shows contextual help in the footer.
    """

    nav_key: str = "setup"
    footer_hint: str = ""

    def compose(self) -> ComposeResult:
        yield Static("", id="sh-header")
        with Horizontal(id="sh-body"):
            with Static(id="sh-sidebar"):
                yield from self._sidebar_items()
            with VerticalScroll(id="sh-content"):
                yield from self.body()
        yield Static("", id="sh-footer")

    # ------------------------------------------------------------------ #
    def body(self):
        """Screen content widgets (override in subclasses)."""
        return []

    def _sidebar_items(self):
        for key, label in NAV_ITEMS:
            marker = "●" if key == self.nav_key else " "
            btn = Button(f" {marker} {label}", id=f"nav-{key}")
            btn.add_class("nav-btn")
            if key == self.nav_key:
                btn.add_class("active")
            btn._nav_key = key  # type: ignore[attr-defined]
            yield btn

    # ------------------------------------------------------------------ #
    def on_mount(self) -> None:
        self._refresh_shell()

    def _refresh_shell(self) -> None:
        cfg = getattr(self.app, "config", {}) or {}
        model = str(cfg.get("provider", {}).get("model", "no model"))
        self.query_one("#sh-header").update(
            f"[b accent]AgentBench[/b accent]  •  [dim]{model}[/dim]"
        )
        self.query_one("#sh-footer").update(self.footer_hint)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        key = getattr(event.button, "_nav_key", None)
        if key:
            self.app.nav_to(key)
        event.stop()