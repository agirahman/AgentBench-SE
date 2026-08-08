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
import threading

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


# --------------------------------------------------------------------------- #
class ExperimentRunner:
    """Real backend runner (Patch 5): executes the core experiment pipeline
    in a worker thread and mirrors it into :class:`BenchmarkState`.

    ``run_experiments`` is blocking, so each run lives in a daemon thread.
    The backend reports a whole ``(issue, strategy)`` in one shot, so each
    completion maps to one ``task.finished`` + a log line; ``run.started`` /
    ``run.finished`` bracket the run. Pause/stop use the cooperative hooks
    ``is_paused`` / ``should_abort`` (polled between issues).

    ``issue_loader`` / ``strategy_factory`` are injectable seams (same idea
    as ``RunCommand``) so tests can run the full pipeline with fakes —
    production defaults load SWE-bench issues and the configured provider
    strategies.

    The SimulatedRunner above remains for demos/tests; this class is what
    the app actually uses (``Runner`` alias below).
    """

    def __init__(
        self,
        state: BenchmarkState,
        config: dict,
        run_params: dict,
        *,
        issue_loader=None,
        strategy_factory=None,
        strategy: str = "all",
        rate_limit_seconds: float | None = None,
        resume: bool = False,
    ) -> None:
        self.state = state
        self.config = config or {}
        self.tasks: list[str] = []
        self.failed: list[str] = []
        self._retry_ids: list[str] | None = None
        self._stop_flag = threading.Event()
        self._pause_flag = threading.Event()
        self._thread: threading.Thread | None = None
        self._issue_loader = issue_loader
        self._strategy_factory = strategy_factory
        self._strategy = strategy
        self._resume = resume
        if rate_limit_seconds is None:
            rate_limit_seconds = float(
                self.config.get("experiment", {}).get("rate_limit", 1.5)
            )
        self._rate_limit = rate_limit_seconds
        self._run_params = dict(run_params or {})
        self._output_dir = str(self._run_params.get("output_dir") or "results")

    # -- public control --------------------------------------------------- #
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def paused(self) -> bool:
        return self._pause_flag.is_set()

    def start(self) -> None:
        if self.running:
            return
        self._thread = threading.Thread(
            target=self._run, name="agentbench-run", daemon=True
        )
        self._thread.start()

    def pause(self) -> None:
        if self.running and not self.paused:
            self._pause_flag.set()
            self.state.log("warn", "paused — finishing current task...")

    def resume(self) -> None:
        if self.paused:
            self._pause_flag.clear()
            self.state.log("info", "resumed")

    def toggle_pause(self) -> bool:
        """Pause if running, resume if paused. Returns the new paused state."""
        if self.paused:
            self.resume()
            return False
        self.pause()
        return True

    def stop(self) -> None:
        self._stop_flag.set()
        self.state.log("warn", "stop requested — finishing current task...")

    def retry_failed(self) -> bool:
        """Re-run only the failed tasks (fresh experiment dir)."""
        if self.running or not self.failed:
            return False
        self._retry_ids = list(self.failed)
        self.start()
        return True

    # -- internals -------------------------------------------------------- #
    def _resolve_repo_specs(self) -> dict[str, int]:
        """repo -> issue-count for the selected task repos."""
        from agentbench.core.experiments.experiment_config import DEFAULT_REPOS

        selected = list(self._run_params.get("tasks") or [])
        repos = self.config.get("dataset", {}) or {}
        repos = repos.get("repos") or DEFAULT_REPOS
        if not selected:
            return dict(repos)
        return {r: int(repos.get(r, DEFAULT_REPOS.get(r, 1))) for r in selected}

    def _build_strategies(self) -> dict:
        if self._strategy_factory is not None:
            return self._strategy_factory()
        from agentbench.commands.run import RunCommand

        cmd = RunCommand(self.config, interactive=False)
        _, strategies = cmd._build_strategies(self._strategy)
        return strategies

    def _load_issues(self) -> list:
        if self._issue_loader is not None:
            return self._issue_loader()
        from agentbench.core.dataset_loader import select_issues

        return select_issues(self._resolve_repo_specs())

    def _run(self) -> None:
        try:
            self._execute()
        except Exception as e:  # noqa: BLE001 - a crashed run must still finish
            self.state.log("error", f"run crashed: {type(e).__name__}: {e}")
            try:
                self.state.run_finished()
            except Exception:  # noqa: BLE001 - never mask the original error
                pass

    def _execute(self) -> None:
        issues = self._load_issues()
        strategies = self._build_strategies()
        if self._retry_ids:
            retry_strats = {t.split("/", 1)[0] for t in self._retry_ids}
            retry_instances = {t.split("/", 1)[1] for t in self._retry_ids}
            strategies = {
                n: s for n, s in strategies.items() if n in retry_strats
            }
            issues = [i for i in issues if i.instance_id in retry_instances]
        if not issues or not strategies:
            self.state.log("warn", "run skipped: no issues/strategies resolved")
            return
        tasks = [
            f"{name}/{issue.instance_id}"
            for name in strategies
            for issue in issues
        ]
        self.tasks = tasks
        self.failed = []
        self.state.run_started(tasks)
        self.state.log(
            "info",
            f"run started: {len(issues)} issue(s) × {len(strategies)} "
            f"strategy = {len(tasks)} task(s) → {self._output_dir}",
        )

        from agentbench.core.experiments.runner import run_experiments

        _df, exp_id = run_experiments(
            issues,
            strategies,
            base_dir=self._output_dir,
            provider_name=str(
                self.config.get("provider", {}).get("name", "unknown")
            ),
            rate_limit_seconds=self._rate_limit,
            resume=self._resume,
            on_issue_complete=self._on_issue_complete,
            should_abort=self._stop_flag.is_set,
            is_paused=self._pause_flag.is_set,
        )
        self.state.log(
            "info",
            f"experiment {exp_id} exported → "
            f"{self._output_dir}/{exp_id}/results.csv",
        )
        self.state.run_finished()

    def _on_issue_complete(
        self,
        instance_id: str,
        strategy: str,
        elapsed: float,
        tokens: int,
        cost_usd: float,
        success: bool,
        status: str,
    ) -> None:
        """Called from the worker thread → state events (thread-safe: the
        UI dispatches them onto the Textual loop via ``call_from_thread``)."""
        task_id = f"{strategy}/{instance_id}"
        # No granular task.progress: the backend reports a whole
        # (issue, strategy) in one shot. Calling task_progress from inside
        # this callback (mid-run_experiments in the worker thread) triggers
        # a CPython segfault in this environment, so it is intentionally
        # skipped — started → finished is the full picture.
        self.state.task_started(task_id)
        self.state.task_finished(
            task_id,
            ok=bool(success),
            time_s=float(elapsed),
            cost_usd=float(cost_usd),
            error=str(status),
        )
        mark = "✓" if success else "✗"
        self.state.log(
            "info" if success else "error",
            f"{mark} {task_id} ({elapsed:.1f}s, {tokens} tok, "
            f"${cost_usd:.4f}) — {status}",
        )
        if not success:
            self.failed.append(task_id)


# The app-level runner slot may hold either implementation (simulated for
# demos/tests, the real backend for production runs).
Runner = SimulatedRunner | ExperimentRunner
