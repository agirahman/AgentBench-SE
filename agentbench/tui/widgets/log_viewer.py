"""LogViewer widget: filterable, searchable log pane (Patch 7, SDD §7.5).

Owns the visible-log pipeline: level filter → search filter → render to a
:class:`~textual.widgets.RichLog`. Deterministic and testable — the widget
keeps ``_entries`` (all) and ``_visible`` (indices passing both filters) so
tests can assert on row counts without parsing terminal output.

Features
--------
- Level filter: ``ALL`` or a minimum severity (``WARN`` shows WARN+ERROR).
- Search: case-insensitive substring on the rendered line.
- Auto-scroll: on by default; toggled via :meth:`toggle_auto_scroll`.
- Expandable stack traces: multi-line messages collapse to their first
  line with a ``[+N lines]`` hint; ``x`` on the cursor row expands.
- Vim-style visual mode: ``v`` anchors, ``j``/``k`` extend, ``y`` yanks
  the selected rows via :meth:`yank_selection` (the screen copies the
  returned text to the clipboard).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import RichLog

from agentbench.tui.state import LOG_LEVELS, LogEntry

_LEVEL_RANK = {level: i for i, level in enumerate(LOG_LEVELS)}  # DEBUG < INFO < WARN < ERROR

# ANSI-ish color per level, rendered via Rich markup.
_LEVEL_COLOR = {
    "DEBUG": "dim",
    "INFO": "cyan",
    "WARN": "yellow",
    "ERROR": "red",
}


def _clock(timestamp: str) -> str:
    """Extract ``HH:MM:SS`` from an ISO timestamp (best effort)."""
    if len(timestamp) >= 19:
        return timestamp[11:19]
    return timestamp


class LogViewer(Widget):
    """Filterable/searchable log pane backing the Logs screen."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("v", "visual_mode", "Visual", show=False),
        Binding("y", "yank", "Yank", show=False),
        Binding("x", "toggle_expand", "Expand", show=False),
    ]

    def __init__(
        self,
        *,
        max_lines: int = 2000,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._entries: list[LogEntry] = []
        self._visible: list[int] = []
        self._level: str = "ALL"
        self._query: str = ""
        self._auto_scroll = True
        self._expanded: set[int] = set()  # indices into _entries
        self._cursor: int = 0  # index into _visible
        self._visual_start: int | None = None  # index into _visible
        self._max_lines = max_lines

    # ------------------------------------------------------------------ #
    # Data ingestion
    # ------------------------------------------------------------------ #
    def set_entries(self, entries: list[LogEntry]) -> None:
        """Replace the full log and re-render."""
        self._entries = list(entries)[-self._max_lines :]
        self._refresh()

    def append(self, entry: LogEntry) -> None:
        """Append one entry and re-render (auto-scrolls when enabled)."""
        self._entries.append(entry)
        if len(self._entries) > self._max_lines:
            del self._entries[: len(self._entries) - self._max_lines]
        self._refresh()

    def clear(self) -> None:
        self._entries = []
        self._refresh()

    # ------------------------------------------------------------------ #
    # Filters
    # ------------------------------------------------------------------ #
    @property
    def level_filter(self) -> str:
        return self._level

    def set_level(self, level: str) -> None:
        """Set the minimum severity; ``ALL`` shows everything."""
        level = level.upper()
        self._level = level if level in LOG_LEVELS else "ALL"
        self._refresh()

    def set_search(self, query: str) -> None:
        self._query = query.strip().lower()
        self._refresh()

    def toggle_auto_scroll(self) -> bool:
        self._auto_scroll = not self._auto_scroll
        return self._auto_scroll

    @property
    def auto_scroll(self) -> bool:
        return self._auto_scroll

    # ------------------------------------------------------------------ #
    # Rendered state
    # ------------------------------------------------------------------ #
    @property
    def visible_count(self) -> int:
        return len(self._visible)

    @property
    def total_count(self) -> int:
        return len(self._entries)

    def line_at(self, visible_index: int) -> str:
        """Formatted line for a visible row (respects expand state)."""
        entry = self._entries[self._visible[visible_index]]
        return self._render_line(entry, visible_index)

    # ------------------------------------------------------------------ #
    # View logic
    # ------------------------------------------------------------------ #
    def _matches(self, entry: LogEntry) -> bool:
        if self._level != "ALL" and _LEVEL_RANK.get(entry.level, 1) < _LEVEL_RANK[self._level]:
            return False
        if self._query and self._query not in self._render_line(entry, 0).lower():
            return False
        return True

    def _refresh(self) -> None:
        self._visible = [i for i, e in enumerate(self._entries) if self._matches(e)]
        if self._cursor >= len(self._visible):
            self._cursor = max(0, len(self._visible) - 1)
        if self._visual_start is not None and self._visual_start >= len(self._visible):
            self._visual_start = None
        if not self.is_mounted:
            return  # unit tests: filter state only; render happens on mount
        log = self.query_one("#log-rich", RichLog)
        log.clear()
        for i in self._visible:
            log.write(self._render_line(self._entries[i], i))
        if self._auto_scroll and self._visible:
            log.scroll_end(animate=False)

    def on_mount(self) -> None:
        # Note: Textual 8.2.8 Widget has no chainable base on_mount.
        self._refresh()

    def _render_line(self, entry: LogEntry, visible_index: int) -> str:
        color = _LEVEL_COLOR.get(entry.level, "")
        label = f"[{color}]{entry.level:>5}[/]" if color else entry.level
        body = entry.message.strip()
        # Collapse multi-line stack traces unless expanded.
        if "\n" in body and visible_index not in self._expanded:
            lines = body.splitlines()
            hint = f"[dim][+{len(lines) - 1} lines][/dim]"
            body = lines[0] + " " + hint
        return f"[dim]{_clock(entry.timestamp)}[/dim] {label} {body}"

    # ------------------------------------------------------------------ #
    # Visual mode (vim-style)
    # ------------------------------------------------------------------ #
    def action_cursor_down(self) -> None:
        if self._visible and self._cursor < len(self._visible) - 1:
            self._cursor += 1
            self._scroll_to_cursor()

    def action_cursor_up(self) -> None:
        if self._visible and self._cursor > 0:
            self._cursor -= 1
            self._scroll_to_cursor()

    def action_visual_mode(self) -> None:
        if self._visual_start is None:
            self._visual_start = self._cursor
            if self.is_mounted:
                self.app.notify("Visual mode — j/k to extend, y to yank")
        else:
            self._visual_start = None

    def action_yank(self) -> None:
        text = self.yank_selection()
        if text and self.is_mounted:
            self.app.copy_to_clipboard(text)

    def action_toggle_expand(self) -> None:
        if not self._visible:
            return
        idx = self._visible[self._cursor]
        if idx in self._expanded:
            self._expanded.discard(idx)
        else:
            self._expanded.add(idx)
        self._refresh()
        self._scroll_to_cursor()

    def yank_selection(self) -> str:
        """Text of the visual-mode range (or the cursor row when no anchor)."""
        if not self._visible:
            return ""
        if self._visual_start is None:
            rows = [self._cursor]
        else:
            lo, hi = sorted((self._visual_start, self._cursor))
            rows = list(range(lo, hi + 1))
        return "\n".join(self.line_at(r) for r in rows)

    def _scroll_to_cursor(self) -> None:
        if not self.is_mounted:
            return  # unit tests: no live widget to scroll
        log = self.query_one("#log-rich", RichLog)
        # RichLog scrolls by rows; cursor row → use scroll_relative from top.
        log.scroll_home(animate=False)
        for _ in range(self._cursor):
            log.scroll_down(animate=False)

    # ------------------------------------------------------------------ #
    # Export / copy helpers
    # ------------------------------------------------------------------ #
    def export_lines(self) -> str:
        """All visible lines joined (used by Export Logs / copy visible)."""
        return "\n".join(self.line_at(i) for i in range(len(self._visible)))

    # ------------------------------------------------------------------ #
    # Compose
    # ------------------------------------------------------------------ #
    def compose(self) -> ComposeResult:
        yield RichLog(
            id="log-rich",
            markup=True,
            highlight=False,
            wrap=False,
            auto_scroll=True,
        )
