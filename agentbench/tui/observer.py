"""Event bus / pub-sub for the AgentBench TUI (SDD §6.2 observer).

Observers subscribe to a :class:`EventBus` and receive every
:class:`StateEvent` emitted by the benchmark state. Screens and widgets
subscribe here instead of talking to each other directly, which keeps the
UI a pure function of state ("WYSIWYG" via events).

Event types used across the TUI::

    config.loaded | config.saved | config.updated
    log.appended
    run.started | run.finished
    task.started | task.progress | task.finished
    screen.changed
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

ObserverFn = Callable[["StateEvent"], None]

_HISTORY_LIMIT = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class StateEvent:
    """A single observable event: ``type`` + opaque ``payload`` dict.

    ``timestamp`` is filled automatically on emit when left empty.
    """

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __str__(self) -> str:
        return f"[{self.timestamp}] {self.type} {self.payload}"


class EventBus:
    """Simple synchronous pub-sub hub with a bounded event history."""

    def __init__(self) -> None:
        self._observers: list[ObserverFn] = []
        self._history: list[StateEvent] = []

    # ------------------------------------------------------------------ #
    # Subscription
    # ------------------------------------------------------------------ #
    def subscribe(self, fn: ObserverFn) -> None:
        """Register an observer. Duplicate subscriptions are ignored."""
        if fn not in self._observers:
            self._observers.append(fn)

    def unsubscribe(self, fn: ObserverFn) -> None:
        """Remove an observer (no-op when it was never subscribed)."""
        if fn in self._observers:
            self._observers.remove(fn)

    @property
    def observer_count(self) -> int:
        return len(self._observers)

    # ------------------------------------------------------------------ #
    # Emission
    # ------------------------------------------------------------------ #
    def emit(self, event: StateEvent) -> None:
        """Deliver ``event`` to every observer (in subscription order)."""
        if not event.timestamp:
            event = StateEvent(event.type, event.payload, _now())
        self._history.append(event)
        if len(self._history) > _HISTORY_LIMIT:
            del self._history[: len(self._history) - _HISTORY_LIMIT]
        for fn in list(self._observers):
            fn(event)

    # ------------------------------------------------------------------ #
    # Introspection (tests / debugging)
    # ------------------------------------------------------------------ #
    @property
    def history(self) -> list[StateEvent]:
        """Recent events, oldest first (bounded to ``_HISTORY_LIMIT``)."""
        return list(self._history)

    def clear_history(self) -> None:
        self._history.clear()
