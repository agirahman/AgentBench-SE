"""Context menu modal (Patch 7, SDD §8.4).

A small :class:`~textual.screen.ModalScreen` offering context-aware copy
actions. Screens push it with the current text + a title; selecting an
action returns the choice via :meth:`ContextMenu.get_result`.

Actions (SDD §8.4):
- ``copy`` — plain text
- ``lines`` — with line numbers
- ``markdown`` — wrapped in a code fence
- ``save`` — write to a file (returned via the result tuple)
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ContextMenu(ModalScreen[tuple[str, str | None]]):
    """Modal with copy actions for a piece of text.

    ``get_result()`` returns ``(action, extra)`` where ``extra`` is the
    save path for ``"save"`` and ``None`` otherwise.
    """

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Close", show=False),
        Binding("q", "dismiss(None)", "Close", show=False),
    ]

    def __init__(self, text: str, title: str = "Copy") -> None:
        super().__init__()
        self._text = text
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="ctx-wrap"):
            yield Static(f"[bold accent]{self._title}[/bold accent]", id="ctx-title")
            preview = self._text.strip().splitlines()
            snippet = "\n".join(preview[:8])
            if len(preview) > 8:
                snippet += f"\n[dim]… {len(preview) - 8} more lines[/dim]"
            yield Static(snippet, id="ctx-preview")
            yield Button("Copy", id="ctx-copy", variant="primary")
            yield Button("Copy with line numbers", id="ctx-lines")
            yield Button("Copy as markdown", id="ctx-markdown")
            yield Button("Save to file", id="ctx-save")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action = {
            "ctx-copy": "copy",
            "ctx-lines": "lines",
            "ctx-markdown": "markdown",
            "ctx-save": "save",
        }.get(event.button.id or "")
        if not action:
            return
        extra: str | None = None
        if action == "save":
            path = Path.cwd() / "agentbench_clipboard_save.txt"
            path.write_text(self._text, encoding="utf-8")
            extra = str(path)
        self.dismiss((action, extra))


def format_with_lines(text: str) -> str:
    """``text`` with 1-based line numbers prefixed."""
    return "\n".join(f"{i:>4}  {ln}" for i, ln in enumerate(text.splitlines(), 1))


def format_as_markdown(text: str) -> str:
    """``text`` wrapped in a fenced code block."""
    return f"```\n{text}\n```"
