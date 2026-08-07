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
from textual.widgets import Footer, Input, RichLog, Static

from agentbench.__version__ import __version__
from agentbench.config_manager import ConfigManager
from agentbench.tui.banner import welcome_banner
from agentbench.tui.commands import COMMAND_HELP


class AgentBenchTUI(App[None]):
    """Textual application wrapping AgentBench-SE commands."""

    CSS = """
    Screen { background: #1a1b26; }
    #banner { height: auto; padding: 0 1; }
    #log { border: round $primary; padding: 0 1; }
    #command-input { dock: bottom; margin: 1 0; }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+space", "focus_input", "Palette"),
        Binding("ctrl+p", "focus_input", "Prompt"),
    ]

    def __init__(self, config: dict | None = None) -> None:
        super().__init__()
        self.config_manager = ConfigManager()
        self.config = config if config is not None else self._load_config()

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
        yield Static(welcome_banner(self.config), id="banner")
        yield RichLog(id="log", markup=True, highlight=True, wrap=True)
        yield Input(
            placeholder="Type a command (run, results, export, config, help) then Enter",
            id="command-input",
        )
        yield Footer()

    def on_mount(self) -> None:
        log = self.query_one("#log", RichLog)
        log.write(
            "[cyan]AgentBench TUI ready. Type [/cyan]"
            "[bold]help[/bold] [cyan]or a command like [/cyan]"
            "[bold]run --issues 2[/bold][cyan].[/cyan]"
        )
        self.query_one("#command-input", Input).focus()

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
            log.write(
                f"[red]Unknown command: '{cmd}'. Try [/red][bold]/help[/bold][red].[/red]"
            )
            return

        capture = io.StringIO()
        console = Console(file=capture, force_terminal=False, width=90)

        def _work() -> None:
            cls(self.config, console, self.config_manager).execute(args)

        def _flush() -> None:
            text = capture.getvalue()
            if text.strip():
                log.write(text)

        def _poll(worker) -> None:
            if worker.is_finished:
                _flush()
            else:
                self.set_timer(0.2, lambda: _poll(worker))

        worker = self.run_worker(_work, thread=True, exit_on_error=False)
        self.set_timer(0.2, lambda: _poll(worker))

    def _show_help(self, log: RichLog) -> None:
        log.write("[bold cyan]Available commands[/bold cyan]")
        for name, desc in COMMAND_HELP.items():
            log.write(f"  [yellow]/{name:<10}[/yellow] {desc}")

    def _show_info(self, log: RichLog) -> None:
        researcher = self.config.get("researcher", {}) or {}
        provider = self.config.get("provider", {}) or {}
        log.write("[bold cyan]AgentBench-SE[/bold cyan]")
        log.write(f"  Version:    {__version__}")
        log.write(f"  Model:      {provider.get('model', 'not set')}")
        log.write(f"  Provider:   {provider.get('name', 'not set')}")
        log.write(
            f"  Researcher: {researcher.get('name', 'guest')} "
            f"({researcher.get('institution', '')})"
        )
        log.write(f"  Config:     {self.config_manager.config_path}")

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