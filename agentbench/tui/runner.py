"""Async experiment runner (SDD §6.3) — simulated backend for Patch 4.

The real backend (``agentbench/commands/run.py``) is wired in Patch 5.
Until then :class:`SimulatedRunner` drives the exact same state lifecycle
the real runner will emit, so the Run screen, progress bars, badges, log
streaming, pause/stop and retry can be built and tested against a
deterministic source:

    run.started
        task.started(task) -> task.progress(done,total) -> task.finished(ok)
    run.finished

The runner is fully async, non-blocking, and honours pause (``asyncio.Event``)
and stop (cooperative flag + task cancellation).
"""

from __future__ import annotations

import asyncio

from agentbench.tui.state import BenchmarkState, TASK_SUCCESS

_STEP_DELAY = 0.05  # per-step sleep for a visible live demo


class SimulatedRunner:
    """Deterministic stand-in runner emitting real state events.

    Args:
        state: The shared :class:`BenchmarkState` to drive.
        tasks: Task ids to execute (e.g. repo keys from the setup form).
        concurrency: Max parallel tasks.
        step_delay: Seconds per simulated step (defaults to a visible
            demo pace; tests pass a tiny value).
    """

    def __init__(self, state: BenchmarkState, tasks: list[str],
                 concurrency: int = 1, step_delay: float = _STEP_DELAY) -> None:
        self.state = state
        self.tasks = list(tasks)
        self.concurrency = max(1, int(concurrency))
        self.step_delay = float(step_delay)
        self.steps_per_task = 5
        self._paused = asyncio.Event()
        self._paused.set()
        self._stop_requested = False
        self._task: asyncio.Task | None = None
        self.failed: list[str] = []

    # ------------------------------------------------------------------ #
    # Control
    # ------------------------------------------------------------------ #
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def paused(self) -> bool:
        return not self._paused.is_set()

    def start(self) -> "SimulatedRunner":
        """Launch the run as an asyncio task (call from the app loop)."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        return self

    def pause(self) -> None:
        self._paused.clear()

    def resume(self) -> None:
        self._paused.set()

    def toggle_pause(self) -> bool:
        """Flip pause state; returns ``True`` when now paused."""
        if self.paused:
            self.resume()
            return False
        self.pause()
        return True

    def stop(self) -> None:
        """Request a cooperative stop; running tasks finish their step."""
        self._stop_requested = True
        self.resume()  # unblock any paused await so the loop can exit

    async def cancel(self) -> None:
        """Hard-cancel the underlying task (used on app shutdown)."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ #
    # Simulation
    # ------------------------------------------------------------------ #
    async def _run(self) -> None:
        self.failed = []
        self.state.run_started(self.tasks)
        self.state.log(
            "info",
            f"run started: {len(self.tasks)} task(s), "
            f"concurrency {self.concurrency}",
        )
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _one(task_id: str, index: int) -> None:
            async with semaphore:
                await self._run_task(task_id, index)

        try:
            await asyncio.gather(
                *(_one(t, i) for i, t in enumerate(self.tasks))
            )
        finally:
            self.state.run_finished()
            if self._stop_requested:
                self.state.log("warn", "run stopped by user")
            else:
                self.state.log("info", "run finished")

    async def _run_task(self, task_id: str, index: int) -> None:
        if self._stop_requested:
            return
        self.state.task_started(task_id)
        self.state.log("info", f"{task_id}: task started")

        # Deterministic outcome: every 3rd task (index 2, 5, 8, ...) fails,
        # so retry has something to chew on during demos/tests.
        ok = index % 3 != 2
        error = "" if ok else "simulated failure (deterministic)"

        for step in range(1, self.steps_per_task + 1):
            await self._paused.wait()
            if self._stop_requested:
                break
            await asyncio.sleep(self.step_delay)
            self.state.task_progress(task_id, step, self.steps_per_task)
            self.state.log(
                "info", f"{task_id}: step {step}/{self.steps_per_task}"
            )
            if step == 2 and not ok:
                self.state.log("warn", f"{task_id}: retry exceeded (simulated)")

        if self._stop_requested:
            self.state.log("warn", f"{task_id}: interrupted by stop")
            return
        if not ok:
            self.failed.append(task_id)
        self.state.task_finished(
            task_id,
            ok=ok,
            score=82.5 if ok else 31.0,
            time_s=round(self.steps_per_task * self.step_delay * 4, 2),
            cost_usd=round(0.012 * self.steps_per_task, 4),
            error=error,
        )
        level = "info" if ok else "error"
        self.state.log(level, f"{task_id}: {'success' if ok else 'FAILED'}")

    # ------------------------------------------------------------------ #
    # Retry (Patch 4 UX; real backend replaces this in Patch 5)
    # ------------------------------------------------------------------ #
    def retry_failed(self) -> bool:
        """Re-run tasks that failed in the last run.

        Returns ``True`` when a retry run was launched. The previous
        successful results are kept (state dedupes by task id).
        """
        failed = [r.task_id for r in self.state.results if r.status != TASK_SUCCESS]
        if not failed or self.running:
            return False
        self.tasks = failed
        self.state.log("warn", f"retrying {len(failed)} failed task(s)")
        self.start()
        return True


# Alias for the app-level runner slot (Patch 5 swaps this import).
Runner = SimulatedRunner  # type: ignore[assignment]
