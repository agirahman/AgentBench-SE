"""Observable benchmark state + config binding (PRD Patch 2, SDD §6.2).

:class:`BenchmarkState` is the single source of truth for the TUI: the
current experiment config, running task statuses, finished results and the
log ring buffer. Every mutation emits a :class:`StateEvent` on the shared
:class:`~agentbench.tui.observer.EventBus` so screens/widgets can re-render
without direct coupling.

Config persistence reuses :class:`agentbench.config_manager.ConfigManager`
(the same backend the CLI ``config`` command uses) — the TUI never
duplicates config logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

from agentbench.config_manager import ConfigManager
from agentbench.tui.observer import EventBus, StateEvent

LOG_LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")

TASK_QUEUED = "queued"
TASK_RUNNING = "running"
TASK_SUCCESS = "success"
TASK_FAIL = "fail"
TASK_STOPPED = "stopped"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Data records
# --------------------------------------------------------------------------- #
@dataclass
class LogEntry:
    """One line in the log ring buffer (SDD §6.2)."""

    timestamp: str
    level: str
    message: str


@dataclass
class TaskStatus:
    """Live status of a single benchmark task."""

    task_id: str
    status: str = TASK_QUEUED
    progress: float = 0.0
    step: int = 0
    total_steps: int = 1
    started_at: str = ""
    finished_at: str = ""


@dataclass
class TaskResult:
    """Final outcome of a finished task (results screen feed)."""

    task_id: str
    status: str = TASK_FAIL
    score: float = 0.0
    time_s: float = 0.0
    cost_usd: float = 0.0
    error: str = ""


class RingBuffer:
    """Fixed-size FIFO; oldest items are dropped when full.

    Used for the log ring (SDD §6.2: max 1000 lines) and mirrors
    ``collections.deque(maxlen=...)`` but with ``maxlen`` configurable at
    runtime and list-style access for tests.
    """

    def __init__(self, maxlen: int = 1000) -> None:
        self.maxlen = max(1, int(maxlen))
        self._items: list[Any] = []

    def append(self, item: Any) -> None:
        self._items.append(item)
        if len(self._items) > self.maxlen:
            del self._items[: len(self._items) - self.maxlen]

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._items)

    def __getitem__(self, index: int) -> Any:
        return self._items[index]

    @property
    def items(self) -> list[Any]:
        return list(self._items)


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
class BenchmarkState:
    """Observable benchmark state.

    Args:
        config: Initial config dict. When ``None`` and a config manager is
            later attached, the manager's file is loaded instead.
        log_ring: Capacity of the log ring buffer (default 1000, SDD §6.2).
    """

    def __init__(self, config: dict | None = None, log_ring: int = 1000) -> None:
        self.events = EventBus()
        self.config_manager: ConfigManager | None = None
        self.config: dict = dict(config) if config is not None else {}
        self.current_screen: str = "setup"
        self.running_tasks: list[TaskStatus] = []
        self.results: list[TaskResult] = []
        self.logs: RingBuffer = RingBuffer(log_ring)
        self._run_active = False

    # ------------------------------------------------------------------ #
    # Config binding (reuses agentbench.config_manager.ConfigManager)
    # ------------------------------------------------------------------ #
    def attach(self, config_manager: ConfigManager) -> "BenchmarkState":
        """Bind a :class:`ConfigManager` and load its config file (if any).

        An explicitly-provided ``config`` (constructor arg) is kept when the
        manager has no file yet.
        """
        self.config_manager = config_manager
        if config_manager.config_exists():
            self.config = self._safe_load()
        return self

    def _safe_load(self) -> dict:
        if self.config_manager is None:
            return self.config
        try:
            return self.config_manager.load()
        except Exception:  # noqa: BLE001 - missing/invalid file => defaults
            return {}

    def load_config(self) -> dict:
        """Reload config from the bound manager and emit ``config.loaded``."""
        self.config = self._safe_load()
        self.emit("config.loaded", {"config": self.config})
        return self.config

    def save_config(self) -> dict:
        """Validate + persist the in-memory config, then emit ``config.saved``."""
        if self.config_manager is None:
            raise RuntimeError("BenchmarkState has no ConfigManager attached")
        validated = self.config_manager.validate(self.config)
        self.config_manager.save(validated)
        self.config = validated
        self.emit("config.saved", {"config": self.config})
        return self.config

    def apply_config(self, updates: dict) -> dict:
        """Deep-merge nested ``updates`` into the config and persist once.

        Unlike calling :meth:`update_config` per key, this validates the
        *complete* config a single time — required for forms that fill
        several fields at once (Patch 3 Setup screen). Emits
        ``config.saved``.
        """
        from agentbench.config_manager import deep_merge

        self.config = deep_merge(self.config, updates)
        return self.save_config()

    def update_config(self, key: str, value: Any) -> dict:
        """Set a dotted config key in memory and persist when bound.

        Example: ``state.update_config("experiment.temperature", 0.3)``.
        Validation errors from :class:`ConfigManager` propagate to the
        caller so the UI can show them inline.
        """
        self.config = _set_dotted(self.config, key, value)
        if self.config_manager is not None:
            validated = self.config_manager.validate(self.config)
            self.config_manager.save(validated)
            self.config = validated
        self.emit("config.updated", {"key": key, "value": value})
        return self.config

    # ------------------------------------------------------------------ #
    # Logging
    # ------------------------------------------------------------------ #
    def log(self, level: str, message: str) -> None:
        """Append a log line to the ring buffer and emit ``log.appended``."""
        level = level.upper() if level.upper() in LOG_LEVELS else "INFO"
        entry = LogEntry(timestamp=_now(), level=level, message=str(message))
        self.logs.append(entry)
        self.emit("log.appended", {"entry": entry, "level": level, "message": str(message)})

    def clear_logs(self) -> None:
        self.logs.clear()

    # ------------------------------------------------------------------ #
    # Run / task lifecycle
    # ------------------------------------------------------------------ #
    @property
    def run_active(self) -> bool:
        return self._run_active

    def run_started(self, tasks: list[str] | None = None) -> None:
        """Begin a run; ``tasks`` (full id list) is echoed in the event
        payload so screens can render the whole queue, not just running
        tasks."""
        self._run_active = True
        self.running_tasks = []
        self.emit("run.started", {"tasks": list(tasks or [])})

    def run_finished(self) -> None:
        self._run_active = False
        self.emit("run.finished", {})

    def task_started(self, task_id: str) -> None:
        """Mark a task as running (replacing any previous entry for the id)."""
        self.running_tasks = [
            t for t in self.running_tasks if t.task_id != task_id
        ] + [TaskStatus(task_id=task_id, status=TASK_RUNNING, started_at=_now())]
        self.emit("task.started", {"task_id": task_id})

    def task_progress(self, task_id: str, done: int, total: int) -> None:
        """Update progress of a running task (``done`` of ``total`` steps)."""
        total = total or 1
        for task in self.running_tasks:
            if task.task_id == task_id:
                task.step = int(done)
                task.total_steps = int(total)
                task.progress = max(0.0, min(1.0, int(done) / total))
                break
        self.emit(
            "task.progress",
            {"task_id": task_id, "done": int(done), "total": int(total)},
        )

    def task_finished(self, task_id: str, ok: bool = True, *, score: float = 0.0,
                      time_s: float = 0.0, cost_usd: float = 0.0,
                      error: str = "") -> None:
        """Finalize a task: update live status and append a :class:`TaskResult`."""
        status = TASK_SUCCESS if ok else TASK_FAIL
        finished_at = _now()
        for task in self.running_tasks:
            if task.task_id == task_id:
                task.status = status
                task.progress = 1.0 if ok else task.progress
                task.finished_at = finished_at
                break
        self.results = [r for r in self.results if r.task_id != task_id] + [
            TaskResult(
                task_id=task_id,
                status=status,
                score=float(score),
                time_s=float(time_s),
                cost_usd=float(cost_usd),
                error=str(error),
            )
        ]
        self.emit(
            "task.finished",
            {"task_id": task_id, "status": status, "ok": ok, "error": str(error)},
        )

    # ------------------------------------------------------------------ #
    # Emission
    # ------------------------------------------------------------------ #
    def emit(self, event_type: str, payload: dict | None = None) -> None:
        """Convenience: build a :class:`StateEvent` and publish it."""
        self.events.emit(StateEvent(event_type, payload or {}))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _set_dotted(config: dict, key: str, value: Any) -> dict:
    """Return a copy of ``config`` with ``key`` (dotted) set to ``value``.

    Intermediate dicts are created when missing so partial keys like
    ``provider.model`` work on an empty config.
    """
    result = {k: (dict(v) if isinstance(v, dict) else v) for k, v in config.items()}
    parts = key.split(".")
    node = result
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value
    return result
