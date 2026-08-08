"""Tests for Patch 4 — Run Screen (dual progress, live log, stop/retry).

Portable across platforms: uses ``asyncio.run`` + the ``_boot`` async
generator (no ``@pytest.mark.asyncio`` — the Windows venv has no plugin).
"""

import asyncio

import pytest

from agentbench.tui.runner import SimulatedRunner
from agentbench.tui.screens.run_screen import RunScreen
from agentbench.tui.state import TASK_FAIL, TASK_SUCCESS


async def _boot(**kwargs):
    from agentbench.tui.app import AgentBenchTUI

    app = AgentBenchTUI(**kwargs)
    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.pause(0.2)
        yield app, pilot


async def _run_screen(app, pilot) -> RunScreen:
    """Navigate to the Run screen and return it (typed).

    ``switch_screen`` is async — pause until the new screen is mounted
    before querying widgets.
    """
    app.nav_to("run")
    await pilot.pause(0.1)
    return app.screen  # type: ignore[return-value]


def _text(widget) -> str:
    return str(getattr(widget, "content", ""))


def _log_text(log) -> str:
    out = []
    for line in log.lines:
        pieces = [seg.text for seg in line if seg.text]
        if pieces:
            out.append("".join(pieces))
    return "\n".join(out)


async def _wait_for(pilot, predicate, timeout=5.0):
    """Pause the app loop until ``predicate()`` is true (or timeout)."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await pilot.pause(0.02)
    return predicate()


# --------------------------------------------------------------------------- #
class TestRunScreenRendering:
    def test_run_screen_mounts_with_widgets(self):
        async def run():
            async for app, pilot in _boot():
                screen = await _run_screen(app, pilot)
                assert screen.query_one("#run-overall") is not None
                assert screen.query_one("#run-overall-label") is not None
                assert screen.query_one("#run-log") is not None
                assert screen.query_one("#run-tasks") is not None
                assert screen.query_one("#run-pause") is not None
                assert screen.query_one("#run-stop") is not None
                assert screen.query_one("#run-retry") is not None
                assert screen.query_one("#run-summary") is not None

        asyncio.run(run())

    def test_empty_state_shows_no_tasks(self):
        async def run():
            async for app, pilot in _boot():
                screen = await _run_screen(app, pilot)
                assert "no tasks" in _text(screen.query_one("#run-overall-label"))
                assert "Running" not in _text(screen.query_one("#run-status"))

        asyncio.run(run())

    def test_task_rows_render_from_state(self):
        async def run():
            async for app, pilot in _boot():
                screen = await _run_screen(app, pilot)
                st = app.state
                st.run_started(["TS001", "TS002"])
                st.task_started("TS001")
                st.task_progress("TS001", 2, 5)
                await pilot.pause(0.05)
                row = screen.query_one("#run-task-TS001")
                assert "TS001" in _text(row)
                assert "40" in _text(row)  # 2/5 → 40%
                # queued task not started yet → no row, no crash
                assert screen.query_one("#run-task-TS002") is not None

        asyncio.run(run())

    def test_overall_label_shows_counts_and_eta(self):
        async def run():
            async for app, pilot in _boot():
                screen = await _run_screen(app, pilot)
                st = app.state
                st.run_started(["A", "B", "C"])
                st.task_started("A")
                st.task_finished("A", ok=True, time_s=1.0)
                await pilot.pause(0.05)
                label = _text(screen.query_one("#run-overall-label"))
                assert "1/3" in label
                assert "33" in label  # 33%
                assert "elapsed" in label
                assert "ETA" in label

        asyncio.run(run())


# --------------------------------------------------------------------------- #
class TestSimulatedRunner:
    def test_runner_lifecycle_emits_events(self):
        async def run():
            from agentbench.tui.state import BenchmarkState

            st = BenchmarkState()
            types = []
            st.events.subscribe(lambda ev: types.append(ev.type))
            runner = SimulatedRunner(st, ["T1", "T2"], step_delay=0.001)
            runner.start()
            while runner.running:
                await asyncio.sleep(0.01)
            assert types[0] == "run.started"
            assert "run.finished" in types

        asyncio.run(run())

    def test_runner_completes_tasks_with_deterministic_failure(self):
        async def run():
            from agentbench.tui.state import BenchmarkState

            st = BenchmarkState()
            runner = SimulatedRunner(st, ["T1", "T2", "T3"], step_delay=0.001)
            runner.start()
            while runner.running:
                await asyncio.sleep(0.02)
            assert len(st.results) == 3
            statuses = {r.task_id: r.status for r in st.results}
            assert statuses["T1"] == TASK_SUCCESS
            assert statuses["T2"] == TASK_SUCCESS
            assert statuses["T3"] == TASK_FAIL  # index 2 → deterministic fail
            assert st.run_active is False

        asyncio.run(run())

    def test_runner_stop_halts_run(self):
        async def run():
            from agentbench.tui.state import BenchmarkState

            st = BenchmarkState()
            runner = SimulatedRunner(st, ["T1", "T2", "T3"], step_delay=0.05)
            runner.start()
            await asyncio.sleep(0.12)
            runner.stop()
            while runner.running:
                await asyncio.sleep(0.02)
            assert st.run_active is False
            assert any("stopped" in e.message for e in st.logs)

        asyncio.run(run())

    def test_retry_failed_reruns_only_failures(self):
        async def run():
            from agentbench.tui.state import BenchmarkState

            st = BenchmarkState()
            runner = SimulatedRunner(st, ["T1", "T2", "T3"], step_delay=0.001)
            runner.start()
            while runner.running:
                await asyncio.sleep(0.02)
            assert runner.failed == ["T3"]
            assert runner.retry_failed() is True
            assert runner.tasks == ["T3"]
            while runner.running:
                await asyncio.sleep(0.02)
            # T3 retried on index 0 → succeeds
            t3 = [r for r in st.results if r.task_id == "T3"][-1]
            assert t3.status == TASK_SUCCESS

        asyncio.run(run())


# --------------------------------------------------------------------------- #
class TestRunScreenLiveUpdates:
    def test_full_run_drives_ui_to_done(self):
        async def run():
            async for app, pilot in _boot():
                screen = await _run_screen(app, pilot)
                runner = SimulatedRunner(
                    app.state, ["T1", "T2", "T3"], step_delay=0.005
                )
                app.runner = runner
                runner.start()
                ok = await _wait_for(pilot, lambda: not runner.running, timeout=5)
                assert ok, "run did not finish in time"
                await pilot.pause(0.05)
                summary = _text(screen.query_one("#run-summary"))
                assert "2 passed" in summary
                assert "1 failed" in summary
                assert "Run selesai" in summary
                log_text = _log_text(screen.query_one("#run-log"))
                assert "T3: FAILED" in log_text

        asyncio.run(run())

    def test_pause_resume_toggles(self):
        async def run():
            async for app, pilot in _boot():
                screen = await _run_screen(app, pilot)
                runner = SimulatedRunner(
                    app.state, ["T1"], step_delay=0.02
                )
                app.runner = runner
                # Pause BEFORE start: deterministic on every platform (no
                # race with the run finishing before we can pause it).
                runner.pause()
                runner.start()
                await pilot.pause(0.15)
                assert runner.running
                assert runner.paused
                assert app.state.running_tasks  # task started, waiting
                step_before = app.state.running_tasks[0].step
                await asyncio.sleep(0.1)
                step_after = app.state.running_tasks[0].step
                assert step_after == step_before  # frozen while paused
                runner.resume()
                ok = await _wait_for(pilot, lambda: not runner.running, timeout=5)
                assert ok

        asyncio.run(run())

    def test_stop_button_stops_run(self):
        async def run():
            async for app, pilot in _boot():
                screen = await _run_screen(app, pilot)
                runner = SimulatedRunner(
                    app.state, ["T1", "T2"], step_delay=0.05
                )
                app.runner = runner
                runner.start()
                await pilot.pause(0.15)
                screen.action_stop_run()
                ok = await _wait_for(pilot, lambda: not runner.running, timeout=5)
                assert ok
                assert app.state.run_active is False
                summary = _text(screen.query_one("#run-summary"))
                assert "dihentikan" in summary

        asyncio.run(run())

    def test_retry_button_reruns_failed(self):
        async def run():
            async for app, pilot in _boot():
                screen = await _run_screen(app, pilot)
                runner = SimulatedRunner(
                    app.state, ["T1", "T2", "T3"], step_delay=0.005
                )
                app.runner = runner
                runner.start()
                ok = await _wait_for(pilot, lambda: not runner.running, timeout=5)
                assert ok
                assert runner.failed == ["T3"]
                screen.action_retry_failed()
                ok = await _wait_for(pilot, lambda: not runner.running, timeout=5)
                assert ok
                t3 = [r for r in app.state.results if r.task_id == "T3"][-1]
                assert t3.status == TASK_SUCCESS

        asyncio.run(run())


# --------------------------------------------------------------------------- #
class TestAppWiring:
    def test_setup_submitted_starts_run_and_navigates(self, tmp_path):
        async def run():
            from test_tui_runner_backend import fake_runner_kwargs

            async for app, pilot in _boot(
                runner_kwargs=fake_runner_kwargs(n_issues=1, delay=0.01)
            ):
                assert app.state.current_screen == "setup"
                app.state.emit(
                    "setup.submitted",
                    {"tasks": ["django/django"], "concurrency": 1,
                     "output_dir": str(tmp_path)},
                )
                ok = await _wait_for(
                    pilot,
                    lambda: app.runner is not None and app.runner.running,
                )
                assert ok, "runner did not start"
                assert app.state.current_screen == "run"
                assert app.runner is not None
                assert app.runner.running  # type: ignore[union-attr]
                # real backend: task ids are strategy/instance pairs
                assert app.runner.tasks == ["direct/EXP-001", "review/EXP-001"]  # type: ignore[union-attr]
                app.runner.stop()  # type: ignore[union-attr]
                ok = await _wait_for(
                    pilot,
                    lambda: app.runner is not None and not app.runner.running,
                )
                assert ok

        asyncio.run(run())

    def test_setup_submitted_empty_tasks_is_noop(self):
        async def run():
            async for app, pilot in _boot():
                app.state.emit("setup.submitted", {"tasks": []})
                await pilot.pause(0.05)
                assert app.state.current_screen == "setup"
                assert app.runner is None

        asyncio.run(run())
