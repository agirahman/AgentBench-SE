"""AgentBench TUI — a professional Textual interface for researchers.

Provides a full-screen command environment on top of the existing command
modules (run / results / export / config / pricing / dataset / artifacts),
so behavior is identical to the classic REPL while the presentation is a
modern TUI with a pyfiglet banner, command palette, and a live status bar.

Run it with:  ``agentbench``   (or ``python -m agentbench.tui.app``).
"""

from __future__ import annotations

import io

from rich.console import Console
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Collapsible, Footer, Input, RichLog

from agentbench.__version__ import __version__
from agentbench.config_manager import ConfigManager
from agentbench.tui.banner import welcome_banner
from agentbench.tui.commands import COMMAND_HELP


class AgentBenchTUI(App[None]):
    """Textual application wrapping AgentBench-SE commands."""

    CSS = """
    Screen { background: #16161e; }
    #log { border: round $primary; padding: 0 1; }
    #command-input { dock: bottom; margin: 1 0; }
    #detail-panel { dock: bottom; height: auto; max-height: 45%; margin: 0 1 3 1; }
    Collapsible { background: $surface; padding: 0 1; }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+space", "focus_input", "Prompt"),
        Binding("ctrl+p", "focus_input", "Prompt"),
        # --- TUI-2: quick command shortcuts (power users) ---
        Binding("ctrl+r", "cmd_run", "Run"),
        Binding("ctrl+s", "cmd_results", "Results"),
        Binding("ctrl+e", "cmd_export", "Export"),
        Binding("ctrl+h", "cmd_help", "Help"),
        Binding("ctrl+l", "clear_log", "Clear log"),
        Binding("ctrl+d", "toggle_details", "Details"),
        Binding("ctrl+y", "copy_log", "Copy log"),
        Binding("ctrl+w", "save_log", "Save log"),
    ]

    def __init__(self, config: dict | None = None, log_dir: str | None = None) -> None:
        super().__init__()
        self.config_manager = ConfigManager()
        self.config = config if config is not None else self._load_config()
        # where `save_log` writes (default: <cwd>/logs)
        self._log_dir = log_dir or "logs"

    # ------------------------------------------------------------------ #
    def _load_config(self) -> dict:
        try:
            return self.config_manager.load()
        except Exception:  # noqa: BLE001
            return {}

    # ------------------------------------------------------------------ #
    # Compose / mount
    # ------------------------------------------------------------------ #
    def compose(self) -> ComposeResult:
        yield RichLog(id="log", markup=True, highlight=True, wrap=True)
        # TUI-2: collapsible detail panel for the last command's full output
        yield Collapsible(
            RichLog(id="detail-log", markup=True, highlight=True, wrap=True),
            title="Details",
            collapsed=True,
            id="detail-panel",
        )
        yield Input(
            placeholder="Type a command (run, results, export, config, help) then Enter",
            id="command-input",
        )
        yield Footer()

    def on_mount(self) -> None:
        # Banner appears once at startup inside the log (scrolls away after),
        # not as a sticky top-pinned widget.
        log = self.query_one("#log", RichLog)
        log.write(welcome_banner(self.config))
        log.write("")
        log.write(
            "Type [bold]help[/bold] for commands, or "
            "[bold]run --issues 2[/bold] to launch an experiment."
        )
        log.write("")
        # Ensure later module output is separated from the banner.
        self.query_one("#command-input", Input).focus()

    @staticmethod
    def _blank(log: RichLog) -> None:
        log.write("")

    @staticmethod
    def _sep(log: RichLog, label: str | None = None) -> None:
        """Blank line before a command's output (keeps results spaced)."""
        log.write("")

    # ------------------------------------------------------------------ #
    # Handling
    # ------------------------------------------------------------------ #
    def _log_ref(self) -> RichLog:
        return self.query_one("#log", RichLog)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = (event.value or "").strip()
        self.query_one("#command-input", Input).value = ""
        if raw:
            command_line = raw.lstrip("/")
            self.run_worker(
                self._run_command(command_line),
                name="dispatch",
                exit_on_error=False,
            )

    def action_focus_input(self) -> None:
        self.query_one("#command-input", Input).focus()

    # ------------------------------------------------------------------ #
    # TUI-2: quick-command actions (keyboard shortcuts)
    # ------------------------------------------------------------------ #
    def _run_input(self, line: str) -> None:
        """Inject a command line into the dispatch pipeline."""
        self.run_worker(
            self._run_command(line),
            name="dispatch",
            exit_on_error=False,
        )

    def action_cmd_run(self) -> None:
        self._run_input("run")

    def action_cmd_results(self) -> None:
        self._run_input("results summary")

    def action_cmd_export(self) -> None:
        self._run_input("export --format markdown")

    def action_cmd_help(self) -> None:
        self._run_input("help")

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()
        self.query_one("#log", RichLog).write("")

    def action_toggle_details(self) -> None:
        """Expand/collapse the detail panel (Collapsible)."""
        collapsible = self.query_one("#detail-panel", Collapsible)
        collapsible.collapsed = not collapsible.collapsed

    # ------------------------------------------------------------------ #
    # copy / save log (so errors & output can be shared)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_text(log: RichLog) -> str:
        """Return the full log content as plain text."""
        out = []
        for line in log.lines:
            pieces = [seg.text for seg in line if seg.text]
            if pieces:
                out.append("".join(pieces))
        return "\n".join(out)

    def action_copy_log(self) -> None:
        """Copy the main log to the clipboard + show a notification."""
        text = self._extract_text(self.query_one("#log", RichLog))
        if not text:
            self.notify("Log is empty.", severity="warning")
            return
        try:
            self.copy_to_clipboard(text)
            self.notify("Copied log to clipboard.")
        except Exception as e:  # noqa: BLE001 - clipboard can fail (WSL/SSH)
            self.notify(f"Clipboard unavailable: {e}", severity="warning")

    def action_save_log(self) -> None:
        """Write the log to a file, then report the absolute path."""
        text = self._extract_text(self.query_one("#log", RichLog))
        if not text:
            self.notify("Log is empty.", severity="warning")
            return
        from pathlib import Path
        from datetime import datetime

        out_dir = Path(self._log_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = out_dir / f"tui-log-{stamp}.txt"
        path.write_text(text, encoding="utf-8")
        self.notify(f"Saved log to {path}")

    # ------------------------------------------------------------------ #
    # Dispatch (coroutine worker)
    # ------------------------------------------------------------------ #
    async def _run_command(self, command_line: str) -> None:
        """Route a command line to native handling or a module command.

        Runs as a Textual worker coroutine. Module commands execute in a
        thread pool (via ``wait_for_object_in_thread``) so long tasks like
        ``run`` don't freeze the UI; their rich output is captured to a
        buffer then rendered into the RichLog.
        """
        parts = (command_line or "").split()
        cmd = (parts[0] or "").lower()
        args = " ".join(parts[1:])
        log = self._log_ref()

        # Add spacing so consecutive results aren't crammed together.
        self._sep(log, f"/{cmd}" if cmd else None)

        # --- native (in-app) commands --------------------------------
        if cmd in ("help", "h"):
            self._show_help(log)
            return
        if cmd in ("info", "i"):
            self._show_info(log)
            return
        if cmd == "version":
            log.write(f"AgentBench-SE version {__version__}")
            return
        if cmd in ("exit", "quit"):
            self.exit()
            return

        # --- module commands -----------------------------------------
        cls = self._command_class(cmd)
        if cls is None:
            log.write(f"Unknown command: '{cmd}'. Try /help.")
            return

        capture = io.StringIO()
        console = Console(file=capture, force_terminal=False, width=90)

        # Live streaming: read the capture buffer incrementally so the user
        # sees output as it is produced instead of after the command ends.
        shown = {"pos": 0}

        def _work() -> None:
            if cmd == "run":
                # RunCommand signature: (config, console, provider_factory,
                # issue_loader, interactive). It does NOT take a config_manager,
                # so don't pass one (it would land in provider_factory and
                # crash with "object is not callable").
                cls(self.config, console, interactive=False).execute(args)
            elif cmd == "config":
                # ConfigCommand is the only one taking a config_manager.
                cls(self.config, console, self.config_manager).execute(args)
            else:
                # results/export/provider/pricing/dataset/artifacts take
                # (config, console) only — passing config_manager would shift
                # it into csv_path or crash on unexpected argument.
                cls(self.config, console).execute(args)

        def _flush() -> None:
            text = capture.getvalue()
            new = text[shown["pos"]:]
            if new.strip():
                self._write_streamed(log, new)
                # mirror raw output into the collapsible detail panel
                dlog = self.query_one("#detail-log", RichLog)
                dlog.write(new)
            shown["pos"] = len(text)

        def _poll(worker) -> None:
            _flush()
            if not worker.is_finished:
                self.set_timer(0.2, lambda: _poll(worker))
            else:
                self._write_done(log, cmd)

        worker = self.run_worker(_work, thread=True, exit_on_error=False)
        self.set_timer(0.2, lambda: _poll(worker))

    @staticmethod
    def _write_streamed(log: RichLog, text: str) -> None:
        """Write streamed output, annotating severity lines (low color)."""
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

    @staticmethod
    def _write_done(log: RichLog, cmd: str) -> None:
        log.write(f"[dim]{cmd} finished[/dim]")
        log.write("")

    def _show_help(self, log: RichLog) -> None:
        log.write("Commands:")
        for name, desc in COMMAND_HELP.items():
            log.write(f"  /{name:<10} {desc}")

    def _show_info(self, log: RichLog) -> None:
        researcher = self.config.get("researcher", {}) or {}
        provider = self.config.get("provider", {}) or {}
        log.write("AgentBench-SE")
        log.write(f"  Version     {__version__}")
        log.write(f"  Model       {provider.get('model', 'not set')}")
        log.write(f"  Provider    {provider.get('name', 'not set')}")
        log.write(
            f"  Researcher  {researcher.get('name', 'guest')}"
            + (f" ({researcher.get('institution', '')})" if researcher.get("institution") else "")
        )
        log.write(f"  Config      {self.config_manager.config_path}")

    @staticmethod
    def _command_class(cmd: str):
        """Map a command name to its command class (imported lazily)."""
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


def main() -> None:
    AgentBenchTUI().run()


if __name__ == "__main__":
    main()