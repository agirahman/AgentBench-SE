"""Central shortcut registry + keyboard-accessibility report (Patch 8).

Single source of truth for every keyboard shortcut shown in the Help
screen and the ``?`` action sheet (SDD §7.6). Bindings are aggregated
from the live ``BINDINGS`` class attributes of each screen, so the
cheatsheet can never drift from what actually works.

The accessibility report (:func:`binding_report`) walks every screen's
bindings and flags actions that have no matching ``action_*`` handler —
the "keyboard-only composable" acceptance criterion (PRD §Patch 8).
"""

from __future__ import annotations

# Global app-level shortcuts (AgentBenchTUI.BINDINGS + shell defaults).
GLOBAL_SHORTCUTS: list[tuple[str, str]] = [
    ("Ctrl+Q", "Keluar aplikasi"),
    ("Ctrl+Space", "Fokus ke input prompt (Console)"),
    ("?", "Action sheet — semua shortcut screen aktif"),
    ("Tab", "Pindah fokus widget berikutnya"),
    ("Shift+Tab", "Pindah fokus widget sebelumnya"),
]

# Copy/paste cheatsheet (SDD §8 — same set across every screen).
COPY_PASTE_SHORTCUTS: list[tuple[str, str]] = [
    ("Ctrl+C", "Copy seleksi mouse (RichLog/TextArea built-in)"),
    ("Ctrl+V", "Paste"),
    ("Ctrl+A", "Select all"),
    ("v", "Visual mode (log) — anchor baris"),
    ("j / k", "Visual mode: perluas pilihan"),
    ("y", "Yank (copy) pilihan visual"),
    ("Esc", "Batal visual mode"),
    ("Alt+C", "Context menu copy (log)"),
    ("K", "Copy tabel results → CSV ke clipboard"),
    ("C", "Export CSV ke file"),
    ("J", "Export JSON ke file"),
]


def _visible_bindings(cls) -> list[tuple[str, str]]:
    """Visible (show=True) bindings of a screen class as ``(key, desc)``."""
    out: list[tuple[str, str]] = []
    for b in getattr(cls, "BINDINGS", []):
        if getattr(b, "show", True):
            out.append((b.key, b.description or b.action))
    return out


def _has_handler(cls, action: str) -> bool:
    """True when ``action_<action>`` exists on the class or any base."""
    name = f"action_{action}"
    return any(hasattr(base, name) for base in cls.__mro__)


def screen_bindings() -> dict[str, list[tuple[str, str]]]:
    """Aggregate visible bindings for every nav screen (sidebar order).

    Import is lazy to avoid a circular import (screens import this
    module; this module only needs the registry at call time).
    """
    from agentbench.tui.app import AgentBenchTUI
    from agentbench.tui.widgets.shell import NAV_ITEMS

    result: dict[str, list[tuple[str, str]]] = {}
    for key, _label in NAV_ITEMS:
        cls = AgentBenchTUI.SCREENS[key]
        result[key] = _visible_bindings(cls)
    return result


def binding_report() -> dict[str, dict]:
    """Keyboard-accessibility report per screen.

    Returns ``{nav_key: {"bindings": [(key, desc), ...], "missing": [action, ...]}}``
    where ``missing`` lists bound actions that resolve to no handler.
    """
    from agentbench.tui.widgets.shell import NAV_ITEMS

    report: dict[str, dict] = {}
    for key, _label in NAV_ITEMS:
        from agentbench.tui.app import AgentBenchTUI

        cls = AgentBenchTUI.SCREENS[key]
        bindings = _visible_bindings(cls)
        missing = [
            b.action
            for b in getattr(cls, "BINDINGS", [])
            if not _has_handler(cls, b.action)
        ]
        report[key] = {"bindings": bindings, "missing": sorted(set(missing))}
    return report
