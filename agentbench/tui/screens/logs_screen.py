"""Logs screen (Patch 7): filterable live log viewer with copy/export.

Replaces the Patch 1 stub. Shows the state ring-buffer log
(:attr:`BenchmarkState.logs`) through a :class:`LogViewer` with:

- level filter (All/DEBUG/INFO/WARN/ERROR),
- live search box,
- auto-scroll toggle,
- expandable stack traces,
- copy (visible / visual-mode selection) to the system clipboard,
- export to a timestamped ``logs-*.txt`` file,
- a right-click style context menu (``Alt+C``) for copy variants.

Live updates: subscribes to ``log.appended`` events exactly like
RunScreen — inline dispatch on the app thread, ``post_message`` bridge
from the worker thread.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Input, Static

from agentbench.tui import clipboard
from agentbench.tui.state import LOG_LEVELS, BenchmarkState
from agentbench.tui.widgets.context_menu import (
    ContextMenu,
    format_as_markdown,
    format_with_lines,
)
from agentbench.tui.widgets.log_viewer import LogViewer
from agentbench.tui.widgets.shell import ShellScreen


class StateEvent(Message):
    """Carries a state event from the worker thread onto the Textual loop.

    Same pattern as RunScreen.StateEvent: ``bubble = False`` so it is
    handled by the screen it was posted to.
    """

    bubble = False

    def __init__(self, event) -> None:
        super().__init__()
        self.event = event


class LogsScreen(ShellScreen):
    """Filterable live log viewer with copy/export."""

    nav_key = "logs"
    footer_hint = (
        "Logs — level filter · / search · s auto-scroll · x expand · "
        "v yank · c copy · Alt+C menu · E export"
    )

    BINDINGS = [
        Binding("/", "focus_search", "Search"),
        Binding("s", "toggle_scroll", "Auto-scroll"),
        Binding("c", "copy_visible", "Copy"),
        Binding("e", "export_logs", "Export"),
        Binding("alt+c", "context_menu", "Menu"),
        Binding("escape", "clear_search", "Clear search", show=False),
    ]

    @property
    def _state(self) -> BenchmarkState:
        """The app's observable state (typed helper)."""
        return self.app.state  # type: ignore[attr-defined]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._app_thread_id = threading.get_ident()

    # ------------------------------------------------------------------ #
    # Compose
    # ------------------------------------------------------------------ #
    def body(self):
        yield Static("", id="logs-status")
        with Horizontal(id="logs-toolbar"):
            yield Static("Level:", classes="logs-label")
            for level in ("ALL", *LOG_LEVELS):
                yield Button(level, id=f"logs-level-{level.lower()}", classes="logs-level")
            yield Input(
                placeholder="Search log… ( / )",
                id="logs-search",
            )
        yield LogViewer(id="logs-viewer")
        with Horizontal(id="logs-actions"):
            yield Button("Auto-scroll: ON", id="logs-scroll-toggle")
            yield Button("Copy", id="logs-copy")
            yield Button("Export", id="logs-export")
            yield Button("Clear", id="logs-clear")

    # ------------------------------------------------------------------ #
    # Mount / state wiring
    # ------------------------------------------------------------------ #
    def on_mount(self) -> None:
        super().on_mount()
        self._app_thread_id = threading.get_ident()
        self._state.events.subscribe(self._on_benchmark_event)
        self._replay()
        self._sync_status()

    def on_unmount(self) -> None:
        self._state.events.unsubscribe(self._on_benchmark_event)

    def _on_benchmark_event(self, event) -> None:
        if threading.get_ident() == self._app_thread_id:
            self._dispatch(event)
        else:
            self.post_message(StateEvent(event))

    def on_state_event(self, message: StateEvent) -> None:
        self._dispatch(message.event)

    def _dispatch(self, event) -> None:
        if event.type == "log.appended":
            entry = event.payload.get("entry")
            if entry is not None:
                self._viewer.append(entry)
                self._sync_status()

    # ------------------------------------------------------------------ #
    # Rendering helpers
    # ------------------------------------------------------------------ #
    @property
    def _viewer(self) -> LogViewer:
        return self.query_one("#logs-viewer", LogViewer)

    def _replay(self) -> None:
        self._viewer.set_entries(self._state.logs.items)

    def _sync_status(self) -> None:
        v = self._viewer
        status = self.query_one("#logs-status", Static)
        status.update(
            f"[bold accent]Logs[/bold accent]  "
            f"[dim]{v.visible_count}/{v.total_count} lines · "
            f"level {v.level_filter} · "
            f"auto-scroll {'ON' if v.auto_scroll else 'OFF'}[/dim]"
        )
        self.query_one("#logs-scroll-toggle", Button).label = (
            f"Auto-scroll: {'ON' if v.auto_scroll else 'OFF'}"
        )

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def action_focus_search(self) -> None:
        self.query_one("#logs-search", Input).focus()

    def action_clear_search(self) -> None:
        inp = self.query_one("#logs-search", Input)
        if inp.value:
            inp.value = ""
        self._viewer.set_search("")
        self._sync_status()

    def action_toggle_scroll(self) -> None:
        self._viewer.toggle_auto_scroll()
        self._sync_status()

    def action_copy_visible(self) -> None:
        text = self._viewer.export_lines()
        if not text:
            self.app.notify("Nothing to copy", severity="warning")
            return
        method, extra = clipboard.copy_text(text)
        self._notify_copied(len(text.splitlines()), method, extra)
        self._state.log("INFO", f"copied {len(text.splitlines())} log lines via {method}")

    def action_export_logs(self) -> None:
        text = self._viewer.export_lines()
        if not text:
            self.app.notify("Nothing to export", severity="warning")
            return
        out_dir = self._export_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = out_dir / f"logs-{stamp}.txt"
        path.write_text(text, encoding="utf-8")
        self.app.notify(f"Exported {len(text.splitlines())} lines → {path}")
        self._state.log("INFO", f"exported log to {path}")

    def action_context_menu(self) -> None:
        text = self._viewer.export_lines()
        if not text:
            self.app.notify("Nothing to copy", severity="warning")
            return
        self.app.push_screen(
            ContextMenu(text, title="Log — copy options"),
            self._handle_context_result,
        )

    def _handle_context_result(self, result: tuple[str, str | None] | None) -> None:
        if not result:
            return
        action, extra = result
        text = self._viewer.export_lines()
        if action == "lines":
            text = format_with_lines(text)
        elif action == "markdown":
            text = format_as_markdown(text)
        elif action == "save":
            self.app.notify(f"Saved → {extra}")
            return
        method, extra2 = clipboard.copy_text(text)
        self._notify_copied(len(text.splitlines()), method, extra2)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _export_dir(self) -> Path:
        out_dir = (
            self._state.config.get("experiment", {}).get("output_dir")
            or "results"
        )
        return Path(str(out_dir)) / "export"

    def _notify_copied(self, n: int, method: str, extra: str | None) -> None:
        if method == "tempfile":
            self.app.notify(
                f"Copied {n} lines → clipboard unavailable, saved to {extra}",
                severity="warning",
            )
        else:
            self.app.notify(f"Copied {n} lines to clipboard ({method})")

    # ------------------------------------------------------------------ #
    # Widget events
    # ------------------------------------------------------------------ #
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("logs-level-"):
            level = bid[len("logs-level-") :].upper()
            self._viewer.set_level(level)
            self._sync_status()
        elif bid == "logs-scroll-toggle":
            self.action_toggle_scroll()
        elif bid == "logs-copy":
            self.action_copy_visible()
        elif bid == "logs-export":
            self.action_export_logs()
        elif bid == "logs-clear":
            self._viewer.clear()
            self._state.clear_logs()
            self._sync_status()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "logs-search":
            self._viewer.set_search(event.value)
            self._sync_status()
