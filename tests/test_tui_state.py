"""Unit tests for Patch 2 — TUI state management & config binding.

Covers: RingBuffer, EventBus (observer.py), BenchmarkState lifecycle
(state.py), and ConfigManager integration (load/save/update from the TUI).
"""

import pytest

from agentbench.config_manager import ConfigManager, ConfigError
from agentbench.tui.observer import EventBus, StateEvent
from agentbench.tui.state import (
    BenchmarkState,
    LogEntry,
    RingBuffer,
    TaskResult,
    TaskStatus,
    TASK_FAIL,
    TASK_RUNNING,
    TASK_SUCCESS,
)


@pytest.fixture
def saved_config(tmp_path, monkeypatch):
    """Save a valid config file and point AGENTBENCH_CONFIG_DIR at it."""
    monkeypatch.setenv("AGENTBENCH_CONFIG_DIR", str(tmp_path))
    cfg = {
        "researcher": {"name": "Agi", "institution": "UNJ", "email": "a@b.com"},
        "provider": {"name": "openrouter", "api_key": "x",
                     "model": "deepseek/deepseek-v4-flash"},
        "experiment": {"temperature": 0.2, "max_retries": 3, "rate_limit": 1.5,
                       "usd_idr_rate": 16500.0},
        "pricing": {},
    }
    ConfigManager(config_path=tmp_path / "config.yaml").save(cfg)
    return cfg


# --------------------------------------------------------------------------- #
# RingBuffer
# --------------------------------------------------------------------------- #
class TestRingBuffer:
    def test_append_and_len(self):
        buf = RingBuffer(maxlen=3)
        buf.append(1)
        buf.append(2)
        assert len(buf) == 2
        assert list(buf) == [1, 2]

    def test_drops_oldest_when_full(self):
        buf = RingBuffer(maxlen=3)
        for i in range(5):
            buf.append(i)
        assert list(buf) == [2, 3, 4]
        assert buf[0] == 2
        assert buf[-1] == 4

    def test_maxlen_clamped(self):
        assert RingBuffer(maxlen=0).maxlen == 1

    def test_clear(self):
        buf = RingBuffer(maxlen=2)
        buf.append("a")
        buf.clear()
        assert len(buf) == 0

    def test_items_returns_copy(self):
        buf = RingBuffer(maxlen=2)
        buf.append(1)
        items = buf.items
        items.append(999)
        assert list(buf) == [1]


# --------------------------------------------------------------------------- #
# EventBus (observer.py)
# --------------------------------------------------------------------------- #
class TestEventBus:
    def test_subscribe_receives_event(self):
        bus = EventBus()
        seen = []
        bus.subscribe(seen.append)
        bus.emit(StateEvent("config.updated", {"key": "a"}))
        assert len(seen) == 1
        ev = seen[0]
        assert ev.type == "config.updated"
        assert ev.payload["key"] == "a"
        assert ev.timestamp  # auto-filled

    def test_timestamp_preserved_when_given(self):
        bus = EventBus()
        seen = []
        bus.subscribe(seen.append)
        bus.emit(StateEvent("x", {}, "2026-01-01T00:00:00+00:00"))
        assert seen[0].timestamp == "2026-01-01T00:00:00+00:00"

    def test_unsubscribe_stops_delivery(self):
        bus = EventBus()
        seen = []
        bus.subscribe(seen.append)
        bus.unsubscribe(seen.append)
        bus.emit(StateEvent("x"))
        assert seen == []

    def test_duplicate_subscribe_ignored(self):
        bus = EventBus()
        bus.subscribe(print)
        bus.subscribe(print)
        assert bus.observer_count == 1

    def test_history_bounded(self):
        bus = EventBus()
        for i in range(250):
            bus.emit(StateEvent("t", {"i": i}))
        assert len(bus.history) == 200
        assert bus.history[0].payload["i"] == 50

    def test_emit_order(self):
        bus = EventBus()
        order = []
        bus.subscribe(lambda ev: order.append("first"))
        bus.subscribe(lambda ev: order.append("second"))
        bus.emit(StateEvent("x"))
        assert order == ["first", "second"]

    def test_observer_removed_during_emit_safe(self):
        """Removing an observer inside emit must not skip remaining ones."""
        bus = EventBus()
        order = []

        def a(ev):
            order.append("a")
            bus.unsubscribe(a)

        def b(ev):
            order.append("b")

        bus.subscribe(a)
        bus.subscribe(b)
        bus.emit(StateEvent("x"))
        assert order == ["a", "b"]


# --------------------------------------------------------------------------- #
# BenchmarkState — logging & lifecycle
# --------------------------------------------------------------------------- #
class TestBenchmarkState:
    def test_log_appends_and_emits(self):
        st = BenchmarkState()
        events = []
        st.events.subscribe(lambda ev: events.append(ev.type))
        st.log("info", "hello")
        assert len(st.logs) == 1
        entry = st.logs[0]
        assert isinstance(entry, LogEntry)
        assert entry.level == "INFO"
        assert entry.message == "hello"
        assert "log.appended" in events

    def test_log_unknown_level_coerced_to_info(self):
        st = BenchmarkState()
        st.log("verbose", "x")
        assert st.logs[0].level == "INFO"

    def test_log_ring_bounded(self):
        st = BenchmarkState(log_ring=5)
        for i in range(10):
            st.log("info", str(i))
        assert len(st.logs) == 5
        assert st.logs[0].message == "5"

    def test_run_lifecycle(self):
        st = BenchmarkState()
        assert st.run_active is False
        st.run_started()
        assert st.run_active is True
        st.run_finished()
        assert st.run_active is False

    def test_task_lifecycle(self):
        st = BenchmarkState()
        st.run_started()
        st.task_started("TS001")
        assert len(st.running_tasks) == 1
        t = st.running_tasks[0]
        assert isinstance(t, TaskStatus)
        assert t.task_id == "TS001"
        assert t.status == TASK_RUNNING

        st.task_progress("TS001", done=2, total=4)
        assert st.running_tasks[0].step == 2
        assert st.running_tasks[0].total_steps == 4
        assert st.running_tasks[0].progress == 0.5

        st.task_finished("TS001", ok=True, score=100.0, time_s=12.3, cost_usd=0.02)
        assert st.running_tasks[0].status == TASK_SUCCESS
        assert len(st.results) == 1
        r = st.results[0]
        assert isinstance(r, TaskResult)
        assert r.status == TASK_SUCCESS
        assert r.score == 100.0
        assert r.time_s == 12.3
        assert r.cost_usd == 0.02

    def test_task_fail_records_error(self):
        st = BenchmarkState()
        st.task_started("TS002")
        st.task_finished("TS002", ok=False, error="Timeout after 30s")
        assert st.results[0].status == TASK_FAIL
        assert st.results[0].error == "Timeout after 30s"

    def test_task_started_replaces_duplicate(self):
        st = BenchmarkState()
        st.task_started("TS001")
        st.task_started("TS001")
        assert len(st.running_tasks) == 1

    def test_task_finished_replaces_duplicate_result(self):
        st = BenchmarkState()
        st.task_started("TS001")
        st.task_finished("TS001", ok=True)
        st.task_finished("TS001", ok=False)
        assert len(st.results) == 1
        assert st.results[0].status == TASK_FAIL

    def test_task_progress_clamps(self):
        st = BenchmarkState()
        st.task_started("TS001")
        st.task_progress("TS001", done=10, total=4)
        assert st.running_tasks[0].progress == 1.0
        st.task_progress("TS001", done=0, total=0)
        assert st.running_tasks[0].progress == 0.0

    def test_emit_types_sequence(self):
        st = BenchmarkState()
        types = []
        st.events.subscribe(lambda ev: types.append(ev.type))
        st.run_started()
        st.task_started("A")
        st.task_progress("A", 1, 2)
        st.task_finished("A", ok=True)
        st.run_finished()
        assert types == [
            "run.started", "task.started", "task.progress",
            "task.finished", "run.finished",
        ]


# --------------------------------------------------------------------------- #
# BenchmarkState — config binding (ConfigManager reuse)
# --------------------------------------------------------------------------- #
class TestConfigBinding:
    def test_attach_loads_file(self, saved_config, tmp_path):
        st = BenchmarkState()
        st.attach(ConfigManager(config_path=tmp_path / "config.yaml"))
        assert st.config["provider"]["model"] == "deepseek/deepseek-v4-flash"

    def test_attach_keeps_explicit_config_without_file(self, tmp_path):
        st = BenchmarkState(config={"provider": {"model": "keep-me"}})
        st.attach(ConfigManager(config_path=tmp_path / "config.yaml"))
        assert st.config["provider"]["model"] == "keep-me"

    def test_attach_missing_file_gives_empty(self, tmp_path):
        st = BenchmarkState()
        st.attach(ConfigManager(config_path=tmp_path / "config.yaml"))
        assert st.config == {}

    def test_load_config_emits(self, saved_config, tmp_path):
        st = BenchmarkState()
        types = []
        st.events.subscribe(lambda ev: types.append(ev.type))
        st.attach(ConfigManager(config_path=tmp_path / "config.yaml"))
        st.load_config()
        assert "config.loaded" in types

    def test_update_config_persists_and_emits(self, saved_config, tmp_path):
        st = BenchmarkState()
        st.attach(ConfigManager(config_path=tmp_path / "config.yaml"))
        types = []
        st.events.subscribe(lambda ev: types.append(ev.type))
        result = st.update_config("experiment.temperature", 0.5)
        assert result["experiment"]["temperature"] == 0.5
        assert "config.updated" in types
        # persisted on disk
        reloaded = ConfigManager(config_path=tmp_path / "config.yaml").load()
        assert reloaded["experiment"]["temperature"] == 0.5

    def test_update_config_invalid_value_raises(self, saved_config, tmp_path):
        st = BenchmarkState()
        st.attach(ConfigManager(config_path=tmp_path / "config.yaml"))
        with pytest.raises(ConfigError):
            st.update_config("experiment.temperature", 5.0)

    def test_save_config_validates_and_persists(self, tmp_path):
        st = BenchmarkState(
            config={
                "researcher": {"name": "Agi"},
                "provider": {"name": "openrouter", "model": "m"},
                "experiment": {"temperature": 0.2},
            }
        )
        st.attach(ConfigManager(config_path=tmp_path / "config.yaml"))
        st.save_config()
        reloaded = ConfigManager(config_path=tmp_path / "config.yaml").load()
        assert reloaded["provider"]["name"] == "openrouter"

    def test_save_config_without_manager_raises(self):
        st = BenchmarkState()
        with pytest.raises(RuntimeError):
            st.save_config()


# --------------------------------------------------------------------------- #
# App integration (Patch 2 hook)
# --------------------------------------------------------------------------- #
class TestAppStateHook:
    def test_app_has_state_with_config(self, saved_config):
        from agentbench.tui.app import AgentBenchTUI

        app = AgentBenchTUI()
        assert app.state is not None
        assert app.config["provider"]["model"] == "deepseek/deepseek-v4-flash"
        assert app.state.config is app.config or app.state.config == app.config

    def test_nav_to_updates_state(self, saved_config):
        import asyncio

        from agentbench.tui.app import AgentBenchTUI

        async def run() -> list:
            app = AgentBenchTUI()
            events = []
            app.state.events.subscribe(lambda ev: events.append(ev.type))
            async with app.run_test(size=(110, 40)) as pilot:
                app.nav_to("results")
                await pilot.pause(0.2)
                return [app.state.current_screen, events]

        screen, events = asyncio.run(run())
        assert screen == "results"
        assert "screen.changed" in events
