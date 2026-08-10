"""Help screen: keyboard cheatsheet (Patch 8, SDD §7.6).

Renders the global shortcuts, per-screen bindings and the copy/paste
cheatsheet from the central registry (:mod:`agentbench.tui.shortcuts`)
so the help can never drift from the real bindings.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static

from agentbench.tui import shortcuts
from agentbench.tui.widgets.shell import NAV_ITEMS, ShellScreen

_NAV_LABEL = {key: label for key, label in NAV_ITEMS}


def _rows(bindings: list[tuple[str, str]]) -> list[str]:
    return [
        f"[b accent]{key:<12}[/b accent] [dim]{desc}[/dim]"
        for key, desc in bindings
    ]


def _section(title: str, rows: list[str]) -> str:
    body = "\n".join(rows) if rows else "[dim]— none —[/dim]"
    return f"[b]{title}[/b]\n{body}"


class HelpScreen(ShellScreen):
    nav_key = "help"
    footer_hint = "Help — cheatsheet shortcut · ? action sheet · Tab fokus"

    def body(self):
        per_screen = shortcuts.screen_bindings()
        screen_sections = [
            _section(
                f"{_NAV_LABEL.get(key, key)}",
                _rows(bindings),
            )
            for key, bindings in per_screen.items()
            if bindings
        ]
        content = "\n\n".join(
            [
                "[b accent]Keyboard Cheatsheet[/b accent]",
                "[dim]Semua shortcut aktif dapat dilihat kapan saja lewat tombol ? "
                "(action sheet).[/dim]\n",
                _section("Navigasi Global", _rows(shortcuts.GLOBAL_SHORTCUTS)),
                *screen_sections,
                _section("Copy & Paste", _rows(shortcuts.COPY_PASTE_SHORTCUTS)),
            ]
        )
        yield Static(content, id="help-content")

    # Convenience re-export for tests: which screen a nav key maps to.
    @staticmethod
    def screen_label(key: str) -> str:
        return _NAV_LABEL.get(key, key)
