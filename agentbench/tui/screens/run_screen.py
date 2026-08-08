"""Run screen: live experiment dashboard (SDD §7.2, Patch 4).

Dual progress (overall bar + per-task rows), live log streaming from the
state ring buffer with level-coloured lines and auto-scroll, status badges
(○ queued / ● running / ✓ success / ✗ fail / ■ stopped), elapsed + ETA,
Pause/Stop/Retry controls and a summary block after the run finishes.

The screen is a pure function of :class:`BenchmarkState` events — it
subscribes to the shared :class:`EventBus` and re-renders on
``run.started``/``run.finished``, ``task.*`` and ``log.appended``. The
runner itself lives in ``agentbench/tui/runner.py`` (simulated in Patch 4;
the real backend is wired in Patch 5).
"""

from __future__ import annotations

import threading
import time

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.widgets import Button, ProgressBar, RichLog, Static

from agentbench.tui.state import (
    TASK_FAIL,
    TASK_QUEUED,
    TASK_RUNNING,
    TASK_STOPPED,
    TASK_SUCCESS,
    BenchmarkState,
)
from agentbench.tui.widgets.shell import ShellScreen

# Status badge -> (glyph, css class)
_BADGES: dict[str, tuple[str, str]] = {
    TASK_QUEUED: ("○", "dim"),
    TASK_RUNNING: ("●", "accent"),
    TASK_SUCCESS: ("✓", "ok"),
    TASK_FAIL: ("✗", "err"),
    TASK_STOPPED: ("■", "warn"),
}

_LOG_STYLE: dict[str, str] = {
    "DEBUG": "dim",
    "INFO": "",
    "WARN": "warn",
    "ERROR": "err",
}

BAR_WIDTH = 14


def _bar(fraction: float, width: int = BAR_WIDTH) -> str:
    """Text progress bar: filled/unfilled block characters."""
    filled = int(round(max(0.0, min(1.0, fraction)) * width))
    return "█" * filled + "░" * (width - filled)


def _fmt_secs(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class StateEvent(Message):
    """Carries a :class:`BenchmarkEvent` from the worker thread onto the
    Textual loop (posted via :meth:`MessagePump.post_message`).

    ``bubble = False``: the message is handled by the screen it was posted
    to; bubbling it up to the app would re-trigger Textual's
    ``_{handler_name}`` fallback on :class:`AgentBenchTUI` (whose
    ``_on_state_event`` is an EventBus callback, not a message handler).
    """

    bubble: ClassVar[bool] = False

    def __init__(self, event) -> None:
        super().__init__()
        self.event = event


class RunScreen(ShellScreen):
    """Live run dashboard bound to BenchmarkState events."""

    nav_key = "run"
    footer_hint = "Run — Space pause · S stop · R retry · L logs · Ctrl+Q quit"

    BINDINGS = [
        Binding("space", "toggle_pause", "Pause"),
        Binding("s", "stop_run", "Stop"),
        Binding("r", "retry_failed", "Retry"),
        Binding("l", "goto_logs", "Logs"),
    ]

    @property
    def _state(self) -> BenchmarkState:
        return self.app.state  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ #
    def body(self):
        yield Static("", id="run-status")
        yield Static("", id="run-overall-label")
        yield ProgressBar(total=1, id="run-overall", show_eta=False)
        yield Static("Tasks", classes="run-section")
        yield VerticalScroll(id="run-tasks", classes="run-list")
        yield Static("Live log", classes="run-section")
        yield RichLog(
            id="run-log", markup=True, highlight=True, wrap=True, auto_scroll=True
        )
        with Horizontal(id="run-actions"):
            yield Button("Pause", id="run-pause", classes="small")
            yield Button("Stop", id="run-stop", classes="danger small")
            yield Button("Retry", id="run-retry", classes="small")
            yield Button("Logs", id="run-logs", classes="small")
        yield Static("", id="run-summary")

    # ------------------------------------------------------------------ #
    # State event wiring
    # ------------------------------------------------------------------ #
    def on_mount(self) -> None:
        super().on_mount()
        self._started_monotonic: float | None = None
        self._stop_requested = False
        self._task_widgets: dict[str, Static] = {}
        # Thread guard: the real runner emits from a worker thread; Textual
        # ``call_from_thread`` refuses to run on the app thread itself.
        self._app_thread_id = threading.get_ident()
        self._state.events.subscribe(self._on_benchmark_event)
        self._sync_task_list()
        self._refresh_overall()
        self._replay_log()

    def on_unmount(self) -> None:
        # Note: Textual 8.2.8 Screen has no base on_unmount to chain to.
        self._state.events.unsubscribe(self._on_benchmark_event)

    def _on_benchmark_event(self, event) -> None:
        """EventBus callback — dispatch state changes onto the Textual loop.

        ``ExperimentRunner`` emits from a worker thread, so bridge via
        ``post_message`` (thread-safe, runs the handler on the app loop in
        the app's own context — ``call_from_thread`` copies the *worker's*
        context, which breaks Textual's ``active_message_pump`` ContextVar).
        Events arriving on the app thread (simulated runner, direct test
        drives) are dispatched inline.

        NOTE: the name deliberately avoids the ``on_*_event`` pattern that
        Textual uses to resolve message handlers (it would be picked up for
        :class:`StateEvent` messages via the ``_{handler_name}`` fallback).
        """
        if threading.get_ident() == self._app_thread_id:
            self._dispatch_state_event(event)
        else:
            self.post_message(StateEvent(event))

    def on_state_event(self, message: StateEvent) -> None:
        self._dispatch_state_event(message.event)

    def _dispatch_state_event(self, event) -> None:
        kind = event.type
        if kind == "run.started":
            self._started_monotonic = time.monotonic()
            self._stop_requested = False
            tasks = event.payload.get("tasks") or []
            self._task_order = list(tasks)
            self._sync_task_list()
            self._set_status("Running", "accent")
            self._refresh_overall()
        elif kind == "run.finished":
            self._refresh_overall()
            self._render_summary()
        elif kind == "task.started":
            self._sync_task_list()
            self._refresh_overall()
        elif kind == "task.progress":
            self._update_task_row(event.payload.get("task_id", ""))
            self._refresh_overall()
        elif kind == "task.finished":
            self._update_task_row(event.payload.get("task_id", ""))
            self._refresh_overall()
        elif kind == "log.appended":
            self._write_log(event.payload.get("entry"))

    # ------------------------------------------------------------------ #
    # Rendering helpers
    # ------------------------------------------------------------------ #
    def _task_ids(self) -> list[str]:
        """Full task id list: explicit order when known, else derived."""
        order: list[str] = list(getattr(self, "_task_order", []) or [])
        if order:
            return order
        seen: list[str] = []
        for t in self._state.running_tasks:
            if t.task_id not in seen:
                seen.append(t.task_id)
        for r in self._state.results:
            if r.task_id not in seen:
                seen.append(r.task_id)
        return seen

    def _status_of(self, task_id: str) -> str:
        for t in self._state.running_tasks:
            if t.task_id == task_id:
                return t.status
        return TASK_QUEUED

    def _progress_of(self, task_id: str) -> float:
        for t in self._state.running_tasks:
            if t.task_id == task_id:
                return t.progress
        return 0.0

    def _step_label(self, task_id: str) -> str:
        for t in self._state.running_tasks:
            if t.task_id == task_id:
                return f"step {t.step}/{t.total_steps}"
        return ""

    def _sync_task_list(self) -> None:
        container = self.query_one("#run-tasks", VerticalScroll)
        order = self._task_ids()
        for task_id in order:
            if task_id not in self._task_widgets:
                row = Static("", id=self._task_row_id(task_id))
                self._task_widgets[task_id] = row
                container.mount(row)
        # Drop rows for tasks that no longer exist (new run, fewer tasks).
        for task_id in list(self._task_widgets):
            if task_id not in order:
                row = self._task_widgets.pop(task_id)
                row.remove()
        for task_id in order:
            self._update_task_row(task_id)

    @staticmethod
    def _task_row_id(task_id: str) -> str:
        # CSS ids must be safe: repo keys contain '/'.
        return f"run-task-{task_id.replace('/', '_')}"

    def _update_task_row(self, task_id: str) -> None:
        row = self._task_widgets.get(task_id)
        if row is None:
            return
        status = self._status_of(task_id)
        glyph, cls = _BADGES.get(status, ("?", "dim"))
        pct = int(round(self._progress_of(task_id) * 100))
        bar = _bar(self._progress_of(task_id))
        step = self._step_label(task_id)
        row.update(
            f"[{cls}]{glyph}[/{cls}] {task_id:<28} "
            f"[{cls}]{bar} {pct:>3}%[/{cls}]  [dim]{step}[/dim]"
        )

    def _refresh_overall(self) -> None:
        order = self._task_ids()
        total = len(order)
        done = sum(
            1
            for tid in order
            if self._status_of(tid) in (TASK_SUCCESS, TASK_FAIL, TASK_STOPPED)
        )
        label = self.query_one("#run-overall-label", Static)
        bar = self.query_one("#run-overall", ProgressBar)
        if total:
            bar.total = total
            bar.progress = done
            pct = int(round(done / total * 100))
            text = f"[dim]{done}/{total} tasks ({pct}%)[/dim]"
        else:
            bar.total = 1
            bar.progress = 0
            text = "[dim]no tasks[/dim]"
        if self._started_monotonic is not None:
            elapsed = time.monotonic() - self._started_monotonic
            eta = self._eta_seconds(done, total, elapsed)
            text += f"  [dim]elapsed {_fmt_secs(elapsed)}[/dim]"
            if eta is not None:
                text += f"  [dim]ETA ~{_fmt_secs(eta)}[/dim]"
        label.update(text)

    def _eta_seconds(self, done: int, total: int, elapsed: float) -> float | None:
        if done <= 0 or total <= done:
            return None
        return elapsed / done * (total - done)

    def _set_status(self, text: str, cls: str = "") -> None:
        tag = f"[{cls}]" if cls else ""
        self.query_one("#run-status", Static).update(f"[b]{tag}{text}[/b]")

    # ------------------------------------------------------------------ #
    # Log streaming
    # ------------------------------------------------------------------ #
    def _write_log(self, entry) -> None:
        if entry is None:
            return
        style = _LOG_STYLE.get(str(entry.level).upper(), "")
        stamp = str(entry.timestamp)[11:19]  # HH:MM:SS from ISO
        markup = (
            f"[dim]{stamp}[/dim] [{style}]{entry.level:<5}[/{style}]"
            if style
            else f"[dim]{stamp}[/dim] {entry.level:<5}"
        )
        self.query_one("#run-log", RichLog).write(f"{markup} {entry.message}")

    def _replay_log(self) -> None:
        """Seed the log pane with the ring buffer (screen remount)."""
        log = self.query_one("#run-log", RichLog)
        log.clear()
        for entry in self._state.logs:
            self._write_log(entry)

    # ------------------------------------------------------------------ #
    # Run controls
    # ------------------------------------------------------------------ #
    def _runner(self):
        return getattr(self.app, "runner", None)

    def action_toggle_pause(self) -> None:
        runner = self._runner()
        if runner is None or not runner.running:
            self.notify("Tidak ada run yang sedang berjalan.", severity="warning")
            return
        paused = runner.toggle_pause()
        self._state.log("info", "run paused" if paused else "run resumed")
        self.query_one("#run-pause", Button).label = (
            "Resume" if paused else "Pause"
        )
        self._set_status("Paused" if paused else "Running", "warn" if paused else "accent")

    def action_stop_run(self) -> None:
        runner = self._runner()
        if runner is None or not runner.running:
            self.notify("Tidak ada run yang sedang berjalan.", severity="warning")
            return
        self._stop_requested = True
        runner.stop()
        self._state.log("warn", "stop requested by user")
        self._set_status("Stopping…", "warn")

    def action_retry_failed(self) -> None:
        runner = self._runner()
        if runner is None:
            self.notify("Belum ada run.", severity="warning")
            return
        if runner.retry_failed():
            self.notify("Retrying failed tasks…")
        else:
            self.notify(
                "Tidak ada task gagal (atau run masih berjalan).",
                severity="warning",
            )

    def action_goto_logs(self) -> None:
        self.app.nav_to("logs")

    # ------------------------------------------------------------------ #
    # Summary (after run.finished)
    # ------------------------------------------------------------------ #
    def _render_summary(self) -> None:
        results = self._state.results
        passed = sum(1 for r in results if r.status == TASK_SUCCESS)
        failed = sum(1 for r in results if r.status == TASK_FAIL)
        stopped = sum(1 for r in results if r.status == TASK_STOPPED)
        total_cost = sum(r.cost_usd for r in results)
        total_time = sum(r.time_s for r in results)
        summary = self.query_one("#run-summary", Static)
        if self._stop_requested:
            status_line = f"[b warn]Run dihentikan oleh user[/b warn]"
        else:
            status_line = f"[b]Run selesai — {passed} passed, {failed} failed"
            if stopped:
                status_line += f", {stopped} stopped"
            status_line += "[/b]"
        summary.update(
            f"{status_line}\n"
            f"[dim]total time {_fmt_secs(total_time)}  •  "
            f"total cost ${total_cost:.4f} USD[/dim]"
        )
        self._set_status("Selesai", "ok" if failed == 0 else "err")

    # ------------------------------------------------------------------ #
    # Widget events
    # ------------------------------------------------------------------ #
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "run-pause":
            self.action_toggle_pause()
            event.stop()
        elif bid == "run-stop":
            self.action_stop_run()
            event.stop()
        elif bid == "run-retry":
            self.action_retry_failed()
            event.stop()
        elif bid == "run-logs":
            self.action_goto_logs()
            event.stop()
        else:
            super().on_button_pressed(event)


# Re-export for typing convenience (used by app.py / tests).
__all__ = ["RunScreen"]
