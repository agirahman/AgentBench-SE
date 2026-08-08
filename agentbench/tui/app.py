"""AgentBench TUI — root app: screen router with a persistent shell.

Patch 1 (PRD/SDD): screens-based app with a shell look (sidebar nav via
the ShellScreen base), 6 stub screens, plus the classic command console
(as "Console" screen) so existing run/results/export still work while the
new screens land in later patches.

Run it with:  ``agentbench tui``.
"""

from __future__ import annotations

import io
import threading
from typing import Any, Callable, ClassVar

from rich.console import Console
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Collapsible, Input, ProgressBar, RichLog, Static

from agentbench.__version__ import __version__
from agentbench.config_manager import ConfigManager
from agentbench.tui.banner import welcome_banner
from agentbench.tui.commands import COMMAND_HELP
from agentbench.tui.screens import (
    ConfigScreen,
    HelpScreen,
    LogsScreen,
    ResultsScreen,
    RunScreen,
    SetupScreen,
)
from agentbench.tui.state import BenchmarkState
from agentbench.tui.widgets.shell import ShellScreen


class ConsoleScreen(ShellScreen):
    """The classic command console as a screen (run/results/export etc.)."""

    nav_key = "console"
    footer_hint = "Console — type a command · Ctrl+C copy · Ctrl+W save"

    BINDINGS = [
        Binding("ctrl+y", "copy_log", "Copy log"),
        Binding("ctrl+w", "save_log", "Save log"),
        Binding("ctrl+l", "clear_log", "Clear"),
        Binding("ctrl+d", "toggle_details", "Details"),
    ]

    def body(self):
        yield Static(welcome_banner(self.app.config), id="console-banner")
        yield RichLog(id="console-log", markup=True, highlight=True, wrap=True)
        yield Collapsible(
            RichLog(id="console-detail", markup=True, highlight=True, wrap=True),
            title="Details",
            collapsed=True,
            id="console-detail-panel",
        )
        yield Static("", id="console-meta")
        yield ProgressBar(total=1, id="console-progress", show_eta=True)
        yield Input(
            placeholder="Type a command (run, results, export, config, help) then Enter",
            id="console-input",
        )

    def on_mount(self) -> None:
        self._refresh_shell()
        self.query_one("#console-input", Input).focus()

    # ------------------------------------------------------------------ #
    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = (event.value or "").strip()
        self.query_one("#console-input", Input).value = ""
        if raw:
            self.run_worker(
                self._dispatch(raw.lstrip("/")), name="dispatch", exit_on_error=False
            )

    async def _dispatch(self, command_line: str) -> None:
        parts = (command_line or "").split()
        cmd = (parts[0] or "").lower()
        args = " ".join(parts[1:])
        log = self.query_one("#console-log", RichLog)
        log.write("")

        if cmd in ("help", "h"):
            self._show_help(log)
            return
        if cmd in ("info", "i"):
            self._show_info(log)
            return
        if cmd == "version":
            log.write(f"AgentBench-SE version {__version__}")
            return

        cls = AgentBenchTUI._command_class(cmd)
        if cls is None:
            log.write(f"Unknown command: '{cmd}'. Try /help.")
            return
        await self._run_module(cmd, args, cls, log)

    def _show_help(self, log: RichLog) -> None:
        log.write("Commands:")
        for name, desc in COMMAND_HELP.items():
            log.write(f"  /{name:<10} {desc}")

    def _show_info(self, log: RichLog) -> None:
        researcher = self.app.config.get("researcher", {}) or {}
        provider = self.app.config.get("provider", {}) or {}
        log.write("AgentBench-SE")
        log.write(f"  Version     {__version__}")
        log.write(f"  Model       {provider.get('model', 'not set')}")
        log.write(f"  Provider    {provider.get('name', 'not set')}")
        log.write(
            f"  Researcher  {researcher.get('name', 'guest')}"
            + (f" ({researcher.get('institution', '')})" if researcher.get("institution") else "")
        )
        log.write(f"  Config      {self.app.config_manager.config_path}")

    async def _run_module(self, cmd: str, args: str, cls, log: RichLog) -> None:
        capture = io.StringIO()
        console = Console(file=capture, force_terminal=False, width=90)
        shown = {"pos": 0}

        def _work() -> None:
            if cmd == "run":
                self.app._show_progress()
                cls(
                    self.app.config,
                    console,
                    interactive=False,
                    progress_cb=self.app._on_run_progress,
                ).execute(args)
            elif cmd == "config":
                cls(self.app.config, console, self.app.config_manager).execute(args)
            else:
                cls(self.app.config, console).execute(args)

        def _flush() -> None:
            text = capture.getvalue()
            new = text[shown["pos"]:]
            if new.strip():
                self._write_streamed(log, new)
                dlog = self.query_one("#console-detail", RichLog)
                dlog.write(new)
            shown["pos"] = len(text)

        def _poll(worker) -> None:
            _flush()
            if not worker.is_finished:
                self.set_timer(0.2, lambda: _poll(worker))
            else:
                if cmd == "run":
                    self.app._hide_progress()
                log.write(f"[dim]{cmd} finished[/dim]")
                log.write("")

        worker = self.run_worker(_work, thread=True, exit_on_error=False)
        self.set_timer(0.2, lambda: _poll(worker))

    @staticmethod
    def _write_streamed(log: RichLog, text: str) -> None:
        for raw in text.splitlines():
            line = raw.rstrip()
            if not line:
                continue
            lowered = line.lower()
            if any(k in lowered for k in ("error", "traceback", "exception")):
                log.write(f"[dim red]{line}[/dim red]")
            elif any(k in lowered for k in ("warning", "warn:", "⚠", "aborted")):
                log.write(f"[dim yellow]{line}[/dim yellow]")
            elif any(k in lowered for k in ("success", "completed", "saved", "✓")):
                log.write(f"[dim green]{line}[/dim green]")
            else:
                log.write(line)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_text(log: RichLog) -> str:
        out = []
        for line in log.lines:
            pieces = [seg.text for seg in line if seg.text]
            if pieces:
                out.append("".join(pieces))
        return "\n".join(out)

    def action_copy_log(self) -> None:
        text = self._extract_text(self.query_one("#console-log", RichLog))
        if not text:
            self.notify("Log is empty.", severity="warning")
            return
        try:
            self.copy_to_clipboard(text)
            self.notify("Copied log to clipboard.")
        except Exception as e:  # noqa: BLE001
            self.notify(f"Clipboard unavailable: {e}", severity="warning")

    def action_save_log(self) -> None:
        text = self._extract_text(self.query_one("#console-log", RichLog))
        if not text:
            self.notify("Log is empty.", severity="warning")
            return
        from datetime import datetime
        from pathlib import Path

        out_dir = Path(self.app._log_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = out_dir / f"tui-log-{stamp}.txt"
        path.write_text(text, encoding="utf-8")
        self.notify(f"Saved log to {path}")

    def action_clear_log(self) -> None:
        log = self.query_one("#console-log", RichLog)
        log.clear()
        log.write("")

    def action_toggle_details(self) -> None:
        collapsible = self.query_one("#console-detail-panel", Collapsible)
        collapsible.collapsed = not collapsible.collapsed


class AgentBenchTUI(App[None]):
    """Root app: boots the setup screen; sidebar navigates between screens."""

    CSS_PATH = "styles.tcss"
    TITLE = "AgentBench-SE"
    SUB_TITLE = f"v{__version__}"

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+space", "focus_input", "Prompt"),
    ]

    # Textual-native screen registry: names used by switch_screen()/nav_to().
    SCREENS: ClassVar[dict[str, Callable[[], Screen[Any]]]] = {
        "setup": SetupScreen,
        "run": RunScreen,
        "results": ResultsScreen,
        "config": ConfigScreen,
        "logs": LogsScreen,
        "help": HelpScreen,
        "console": ConsoleScreen,
    }

    # Boot the app directly into the Setup screen (Textual 8: the screen
    # registry is installed at startup and DEFAULT_MODE resolves it; pushing
    # screens from App.on_mount is not supported on this Textual version).
    MODES: ClassVar[dict[str, str | Callable[[], Screen[Any]]]] = {
        "default": "setup",
    }
    DEFAULT_MODE: ClassVar[str] = "default"

    def __init__(
        self,
        config: dict | None = None,
        log_dir: str | None = None,
        runner_kwargs: dict | None = None,
    ) -> None:
        super().__init__()
        self.config_manager = ConfigManager()
        # Patch 2: observable state owns the config; the app keeps ``self.config``
        # as a convenience mirror for the legacy command console.
        self.state = BenchmarkState(config=config).attach(self.config_manager)
        self.config = self.state.config
        self._log_dir = log_dir or "logs"
        # Patch 4/5: active experiment runner (real backend in a worker
        # thread). Owned by the app so RunScreen and SetupScreen can
        # start/stop it without knowing the implementation.
        from agentbench.tui.runner import Runner

        self.runner: Runner | None = None
        # Test seam: extra kwargs forwarded to Runner() (issue_loader,
        # strategy_factory, rate_limit_seconds, ...).
        self._runner_kwargs = dict(runner_kwargs or {})

    def _load_config(self) -> dict:
        """(Legacy helper) — state owns config; kept for back-compat."""
        try:
            return self.config_manager.load()
        except Exception:  # noqa: BLE001
            return {}

    # ------------------------------------------------------------------ #
    def on_mount(self) -> None:
        # Textual 8.2.8 quirk: the MODES-initial screen never receives a
        # result callback, so the first switch_screen() pops an empty stack
        # (IndexError: pop from empty list). Seed one, mirroring exactly
        # what switch_screen() itself does after popping.
        screen = self.screen
        screen._push_result_callback(screen, None)  # type: ignore[attr-defined]
        # Patch 4: the Run screen reacts to setup submissions (form Start).
        self._app_thread_id = threading.get_ident()
        self.state.events.subscribe(self._on_state_event)

    def on_unmount(self) -> None:
        # Note: Textual 8.2.8 Screen has no base on_unmount to chain to.
        self.state.events.unsubscribe(self._on_state_event)

    def _on_state_event(self, event) -> None:
        """App-level state events: start the run when the form submits.

        Only the ``setup.submitted`` event matters here, and it is always
        emitted from the UI thread; worker-thread emissions (task/progress
        events) are handled by :class:`RunScreen` via its own bridge, so we
        ignore them (avoids a blocking ``call_from_thread`` per event).
        """
        if threading.get_ident() != self._app_thread_id:
            return
        # Safety net: a StateEvent *message* (posted to RunScreen) must never
        # be dispatched here — only EventBus StateEvent objects (with .type).
        if not hasattr(event, "type"):
            return
        self._dispatch_state_event(event)

    def _dispatch_state_event(self, event) -> None:
        if event.type == "setup.submitted":
            self._start_run(event.payload)

    def _start_run(self, payload: dict) -> None:
        """Launch an experiment run from setup-form parameters (Patch 4/5).

        Creates the real backend runner (:class:`ExperimentRunner`),
        navigates to the Run screen and starts the run in a worker thread.
        """
        from agentbench.tui.runner import ExperimentRunner, Runner

        tasks = list(payload.get("tasks") or [])
        output_dir = str(payload.get("output_dir") or "./results")
        if not tasks:
            self.state.log("warn", "run skipped: no tasks selected")
            return
        self.state.log(
            "info",
            f"starting run: {len(tasks)} task(s), output {output_dir}",
        )
        self.runner = ExperimentRunner(self.state, self.config, payload, **self._runner_kwargs)
        self.nav_to("run")
        assert self.runner is not None
        self.runner.start()

    def nav_to(self, key: str) -> None:
        """Navigate to a screen by nav key (setup/run/results/.../console)."""
        if key in self.SCREENS:
            self.switch_screen(key)
            self.state.current_screen = key
            self.state.emit("screen.changed", {"screen": key})

    # ------------------------------------------------------------------ #
    # Progress feedback (used by the Console screen)
    # ------------------------------------------------------------------ #
    def _show_progress(self) -> None:
        try:
            # Query the ACTIVE screen: App.query_one() targets the default
            # screen, so progress widgets on the console screen would be missed.
            pb = self.screen.query_one("#console-progress", ProgressBar)
            pb.total = 1
            pb.progress = 0
            pb.add_class("visible")
            self.screen.query_one("#console-meta", Static).update("Running...")
        except Exception:  # noqa: BLE001
            pass

    def _hide_progress(self) -> None:
        try:
            self.screen.query_one("#console-progress", ProgressBar).remove_class("visible")
            self.screen.query_one("#console-meta", Static).update("")
        except Exception:  # noqa: BLE001
            pass

    def _on_run_progress(self, done: int, total: int, line: str) -> None:
        import threading

        def _update() -> None:
            try:
                pb = self.screen.query_one("#console-progress", ProgressBar)
                pb.total = total
                pb.progress = done
                clean = line.strip().lstrip("/").strip()
                self.screen.query_one("#console-meta", Static).update(clean)
            except Exception:  # noqa: BLE001
                pass

        if self._thread_id == threading.get_ident():
            _update()
        else:
            self.call_from_thread(_update)

    @staticmethod
    def _command_class(cmd: str):
        from agentbench.commands.artifacts import ArtifactsCommand
        from agentbench.commands.config import ConfigCommand
        from agentbench.commands.dataset import DatasetCommand
        from agentbench.commands.export import ExportCommand
        from agentbench.commands.pricing import PricingCommand
        from agentbench.commands.provider import ProviderCommand
        from agentbench.commands.results import ResultsCommand
        from agentbench.commands.run import RunCommand

        return {
            "run": RunCommand,
            "results": ResultsCommand,
            "export": ExportCommand,
            "config": ConfigCommand,
            "provider": ProviderCommand,
            "pricing": PricingCommand,
            "dataset": DatasetCommand,
            "artifacts": ArtifactsCommand,
        }.get(cmd)

    def action_focus_input(self) -> None:
        try:
            self.screen.query_one("#console-input", Input).focus()
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    AgentBenchTUI().run()


if __name__ == "__main__":
    main()