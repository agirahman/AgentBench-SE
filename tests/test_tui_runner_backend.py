"""Tests for Patch 5 — real backend runner (ExperimentRunner) + Run screen.

The core experiment pipeline is exercised with fake issues/strategies
(no provider, no API, no network), mirroring the injectable seams that
``RunCommand`` already offers. UI tests verify the worker-thread → state
→ Textual-loop bridge (``call_from_thread``).

Portable: ``asyncio.run`` + ``_boot`` async generator (no pytest-asyncio).
"""

import asyncio
import time

import pytest

from agentbench.core.models.issue import Issue
from agentbench.core.models.patch import Patch
from agentbench.core.models.result import (
    CostSummary,
    EvaluationResult,
    ExecutionResult,
    ExperimentResult,
)
from agentbench.core.models.inference import InferenceRun
from agentbench.tui.runner import ExperimentRunner
from agentbench.tui.screens.run_screen import RunScreen
from agentbench.tui.state import TASK_FAIL, TASK_SUCCESS


# --------------------------------------------------------------------------- #
# Shared fakes (imported by test_core_runner_control too)
# --------------------------------------------------------------------------- #
def fake_issue(instance_id: str = "EXP-001", repo: str = "django/django") -> Issue:
    return Issue(
        instance_id=instance_id,
        repo=repo,
        base_commit="abc123",
        problem_statement="Fix the bug.",
        hints="",
    )


def fake_issues(n: int = 2, prefix: str = "EXP") -> list[Issue]:
    return [fake_issue(f"{prefix}-{i:03d}") for i in range(1, n + 1)]


def fake_result(instance_id: str = "EXP-001", strategy: str = "direct",
                ok: bool = True) -> ExperimentResult:
    run = InferenceRun(patch="PATCH OK" if ok else "", inferences=[])
    return ExperimentResult(
        instance_id=instance_id,
        strategy=strategy,
        model="fake",
        execution=ExecutionResult(run=run),
        cost=CostSummary(0.001, 0.002, 0.003, 50.0, "test"),
        evaluation=EvaluationResult(success=ok, error="" if ok else "boom"),
        difficulty="easy",
    )


class FakeStrategy:
    """Strategy double: ``run()`` returns immediately (or after ``delay``)."""

    # extract_diff must recognise the response as a real patch
    # (_check_patch_syntax requires a leading ``diff --git`` line).
    _PATCH_OK = (
        "```diff\ndiff --git a/main.py b/main.py\n"
        "--- a/main.py\n+++ b/main.py\n"
        "@@ -1 +1 @@\n-old line\n+new line\n```"
    )

    def __init__(self, ok: bool = True, delay: float = 0.01) -> None:
        self.ok = ok
        self.delay = delay

    def run(self, issue):
        time.sleep(self.delay)
        return Patch(response=self._PATCH_OK if self.ok else ""), fake_result(
            instance_id=issue.instance_id, ok=self.ok
        )


def fake_strategies(delay: float = 0.01) -> dict:
    return {"direct": FakeStrategy(ok=True, delay=delay),
            "review": FakeStrategy(ok=True, delay=delay)}


def fake_runner_kwargs(n_issues: int = 2, delay: float = 0.01,
                       ok_mapping=None) -> dict:
    """kwargs for ExperimentRunner / app runner_kwargs with fakes."""
    ok_map = ok_mapping or {}

    def loader():
        return fake_issues(n_issues)

    def factory():
        return {
            name: FakeStrategy(ok=ok_map.get(name, True), delay=delay)
            for name in ("direct", "review")
        }

    return {"issue_loader": loader, "strategy_factory": factory,
            "rate_limit_seconds": 0.0}


# --------------------------------------------------------------------------- #
async def _boot(**kwargs):
    from agentbench.tui.app import AgentBenchTUI

    app = AgentBenchTUI(**kwargs)
    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.pause(0.2)
        yield app, pilot


async def _wait_for(pilot, predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await pilot.pause(0.02)
    return predicate()


def _text(widget) -> str:
    return str(getattr(widget, "content", ""))


# --------------------------------------------------------------------------- #
class TestExperimentRunner:
    def test_lifecycle_maps_all_tasks_to_state(self, tmp_path):
        async def run():
            from agentbench.tui.state import BenchmarkState

            st = BenchmarkState()
            runner = ExperimentRunner(
                st, {"experiment": {"rate_limit": 0.0}},
                {"tasks": ["django/django"], "output_dir": str(tmp_path)},
                **fake_runner_kwargs(n_issues=2),
            )
            runner.start()
            while runner.running:
                await asyncio.sleep(0.02)
            assert len(st.results) == 4  # 2 issues × 2 strategies
            assert len(runner.tasks) == 4
            assert all(r.status == TASK_SUCCESS for r in st.results)
            assert runner.failed == []
            assert st.run_active is False
            # task ids are strategy/instance pairs
            ids = {r.task_id for r in st.results}
            assert "direct/EXP-001" in ids
            assert "review/EXP-002" in ids

        asyncio.run(run())

    def test_failures_are_collected(self, tmp_path):
        async def run():
            from agentbench.tui.state import BenchmarkState

            st = BenchmarkState()
            kw = fake_runner_kwargs(n_issues=1, ok_mapping={"review": False})
            runner = ExperimentRunner(
                st, {"experiment": {"rate_limit": 0.0}},
                {"tasks": [], "output_dir": str(tmp_path)}, **kw,
            )
            runner.start()
            while runner.running:
                await asyncio.sleep(0.02)
            statuses = {r.task_id: r.status for r in st.results}
            assert statuses["direct/EXP-001"] == TASK_SUCCESS
            assert statuses["review/EXP-001"] == TASK_FAIL
            assert runner.failed == ["review/EXP-001"]

        asyncio.run(run())

    def test_stop_aborts_cooperatively(self, tmp_path):
        async def run():
            from agentbench.tui.state import BenchmarkState

            st = BenchmarkState()
            runner = ExperimentRunner(
                st, {"experiment": {"rate_limit": 0.0}},
                {"tasks": [], "output_dir": str(tmp_path)},
                **fake_runner_kwargs(n_issues=8, delay=0.05),
            )
            runner.start()
            await asyncio.sleep(0.35)  # a couple of tasks done
            runner.stop()
            while runner.running:
                await asyncio.sleep(0.02)
            assert st.run_active is False
            assert len(st.results) < 16  # partial, not everything
            assert any("stop requested" in e.message for e in st.logs)

        asyncio.run(run())

    def test_pause_halts_progress(self, tmp_path):
        async def run():
            from agentbench.tui.state import BenchmarkState

            st = BenchmarkState()
            runner = ExperimentRunner(
                st, {"experiment": {"rate_limit": 0.0}},
                {"tasks": [], "output_dir": str(tmp_path)},
                **fake_runner_kwargs(n_issues=20, delay=0.005),
            )
            runner.start()
            while not runner.tasks:
                await asyncio.sleep(0.02)
            runner.pause()
            assert runner.paused
            await asyncio.sleep(0.06)  # let the in-flight task finish
            before = len(st.results)
            await asyncio.sleep(0.3)
            after = len(st.results)
            assert after == before  # frozen at the next-issue gate
            runner.resume()
            while runner.running:
                await asyncio.sleep(0.02)
            assert st.run_active is False
            assert len(st.results) == 40  # 20 issues × 2 strategies

        asyncio.run(run())

    def test_retry_failed_reruns_only_failures(self, tmp_path):
        async def run():
            from agentbench.tui.state import BenchmarkState

            st = BenchmarkState()
            kw = fake_runner_kwargs(n_issues=2, ok_mapping={"review": False})
            runner = ExperimentRunner(
                st, {"experiment": {"rate_limit": 0.0}},
                {"tasks": [], "output_dir": str(tmp_path)}, **kw,
            )
            runner.start()
            while runner.running:
                await asyncio.sleep(0.02)
            assert set(runner.failed) == {"review/EXP-001", "review/EXP-002"}
            assert runner.retry_failed() is True
            assert runner.tasks == ["review/EXP-001", "review/EXP-002"]
            while runner.running:
                await asyncio.sleep(0.02)
            # retried run: same fakes → review still fails (deterministic),
            # but only 2 tasks were attempted; task_finished replaces the
            # previous result for the same task_id, so 2 entries remain
            retried = [r for r in st.results if r.task_id.startswith("review/")]
            assert len(retried) == 2
            assert all(r.status == TASK_FAIL for r in retried)

        asyncio.run(run())


# --------------------------------------------------------------------------- #
class TestBackendWithUI:
    def test_backend_run_updates_run_screen(self, tmp_path):
        async def run():
            async for app, pilot in _boot(
                runner_kwargs=fake_runner_kwargs(n_issues=2, delay=0.01)
            ):
                app.state.emit(
                    "setup.submitted",
                    {"tasks": ["django/django"], "concurrency": 1,
                     "output_dir": str(tmp_path)},
                )
                ok = await _wait_for(
                    pilot,
                    lambda: app.runner is not None and not app.runner.running,
                )
                assert ok, "backend run did not finish"
                await pilot.pause(0.1)
                screen = app.screen  # type: ignore[assignment]
                assert isinstance(screen, RunScreen)
                from textual.widgets import RichLog, Static

                summary = _text(screen.query_one("#run-summary", Static))
                assert "4 passed" in summary
                assert "0 failed" in summary
                log = screen.query_one("#run-log", RichLog)
                text = "\n".join(
                    "".join(seg.text for seg in line if seg.text)
                    for line in log.lines
                )
                assert "direct/EXP-001" in text
                assert "✓" in text

        asyncio.run(run())

    def test_worker_thread_events_are_thread_safe(self, tmp_path):
        """Events from the worker thread must reach the UI without touching
        Textual widgets off-loop (the call_from_thread bridge)."""
        async def run():
            async for app, pilot in _boot(
                runner_kwargs=fake_runner_kwargs(n_issues=3, delay=0.01)
            ):
                app.state.emit(
                    "setup.submitted",
                    {"tasks": ["django/django"], "output_dir": str(tmp_path)},
                )
                ok = await _wait_for(
                    pilot,
                    lambda: app.runner is not None and not app.runner.running,
                )
                assert ok
                await pilot.pause(0.1)
                screen = app.screen  # type: ignore[assignment]
                # every task row rendered with a badge
                for task_id in app.runner.tasks:  # type: ignore[union-attr]
                    row_id = f"run-task-{task_id.replace('/', '_')}"
                    assert screen.query_one(f"#{row_id}") is not None
                assert "Selesai" in _text(screen.query_one("#run-status"))

        asyncio.run(run())

    def test_stop_button_stops_backend_run(self, tmp_path):
        async def run():
            async for app, pilot in _boot(
                runner_kwargs=fake_runner_kwargs(n_issues=8, delay=0.05)
            ):
                app.state.emit(
                    "setup.submitted",
                    {"tasks": ["django/django"], "output_dir": str(tmp_path)},
                )
                await _wait_for(
                    pilot,
                    lambda: app.runner is not None and app.runner.running,
                )
                # ``_start_run`` assigns the runner BEFORE ``nav_to("run")``
                # completes, so the active screen may still be the setup form
                # at this point — wait for the Run screen to be live.
                await _wait_for(pilot, lambda: app.state.current_screen == "run")
                await pilot.pause(0.05)
                screen = app.screen  # type: ignore[assignment]
                screen.action_stop_run()  # type: ignore[attr-defined]
                ok = await _wait_for(
                    pilot,
                    lambda: app.runner is not None and not app.runner.running,
                )
                assert ok
                assert app.state.run_active is False
                assert "dihentikan" in _text(screen.query_one("#run-summary"))

        asyncio.run(run())
