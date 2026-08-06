"""Interactive shell for AgentBench-SE (cmd.Cmd based REPL).

Commands implemented in Phase 2: help, config, info, version, exit/quit/EOF.
Commands run/results/export/provider/dataset/artifacts land in Phase 3-5 and
are dispatched lazily to their command modules as they become available.
"""

from __future__ import annotations

import cmd

from rich.console import Console
from rich.table import Table

from agentbench.__version__ import __version__
from agentbench.config_manager import ConfigManager
from agentbench.ui.banner import display_banner


class AgentBenchShell(cmd.Cmd):
    """REPL exposing AgentBench-SE functionality to the researcher."""

    prompt = "\nagentbench> "

    def __init__(self, config: dict | None = None, console: Console | None = None):
        super().__init__()
        self.config_manager = ConfigManager()
        self.console = console or Console()
        self.config = config or self.config_manager.load()

    # ------------------------------------------------------------------ #
    # REPL lifecycle
    # ------------------------------------------------------------------ #
    def preloop(self) -> None:
        display_banner(self.console, self.config)

    def emptyline(self) -> bool:
        """Do nothing on an empty line (continue the loop)."""
        return False

    def default(self, line: str) -> None:
        cmd_name = line.split()[0] if line.strip() else ""
        self.console.print(
            f"[red]Unknown command: '{cmd_name}'. Type 'help' for available commands.[/red]"
        )

    # ------------------------------------------------------------------ #
    # Help
    # ------------------------------------------------------------------ #
    def do_help(self, arg: str) -> None:
        """Show available commands or help for a specific command."""
        if arg:
            super().do_help(arg)
            return
        table = Table(title="Available Commands", show_header=False, box=None)
        table.add_column("Category", style="cyan")
        table.add_column("Commands")
        table.add_row("Experiment", "run, resume, status")
        table.add_row("Analysis", "results, compare, export")
        table.add_row("Configuration", "config, setup, provider")
        table.add_row("Data", "dataset, artifacts")
        table.add_row("Help", "help, info, version, exit")
        self.console.print(table)

    # ------------------------------------------------------------------ #
    # Phase 2 commands
    # ------------------------------------------------------------------ #
    def do_config(self, arg: str) -> None:
        """Manage configuration.
        Usage: config <show|set <key> <value>|reset>"""
        from agentbench.commands.config import ConfigCommand

        ConfigCommand(self.config, self.console, self.config_manager).execute(arg)
        # Reflect any changes (config set) in the shell's in-memory state
        self.config = self.config_manager.load()

    def do_setup(self, arg: str) -> None:
        """Re-run the interactive setup wizard."""
        from agentbench.setup_wizard import run_setup

        run_setup()
        # Reload config so the shell reflects the new values
        self.config = self.config_manager.load()

    def do_info(self, arg: str) -> None:
        """Show framework information."""
        from rich.panel import Panel

        researcher = self.config.get("researcher", {}) or {}
        panel = Panel.fit(
            f"[bold]Framework:[/bold] AgentBench-SE v{__version__}\n"
            f"[bold]Researcher:[/bold] {researcher.get('name', 'Unknown')}\n"
            f"[bold]Institution:[/bold] {researcher.get('institution', 'N/A')}\n\n"
            f"[bold]Paths:[/bold]\n"
            f"  Config: {self.config_manager.config_path}\n"
            f"  Results: ./results/\n"
            f"  Dataset: {self.config_manager.dataset_cache_dir}",
            title="AgentBench-SE Information",
            border_style="cyan",
        )
        self.console.print(panel)

    def do_version(self, arg: str) -> None:
        """Show version."""
        self.console.print(f"AgentBench-SE version {__version__}")

    # ------------------------------------------------------------------ #
    # Exit
    # ------------------------------------------------------------------ #
    def do_exit(self, arg: str) -> bool:
        """Exit interactive shell."""
        self.console.print("\nGoodbye! 👋")
        return True

    def do_quit(self, arg: str) -> bool:
        """Exit interactive shell (alias for exit)."""
        return self.do_exit(arg)

    def do_EOF(self, arg: str) -> bool:
        """Handle Ctrl+D (EOF)."""
        return self.do_exit(arg)

    # ------------------------------------------------------------------ #
    # Phase 3-5 stubs (wired as the command modules land)
    # ------------------------------------------------------------------ #
    def do_run(self, arg: str) -> None:
        """Start experiment run.
        Usage: run [--issues N] [--strategy all|direct|planning|review] [--output DIR] [--resume]"""
        from agentbench.commands.run import RunCommand

        RunCommand(self.config, self.console).execute(arg)

    def do_results(self, arg: str) -> None:
        """View experiment results (Phase 4)."""
        self._not_ready("results")

    def do_export(self, arg: str) -> None:
        """Export results (Phase 4)."""
        self._not_ready("export")

    def do_provider(self, arg: str) -> None:
        """Show/test provider connection (Phase 5)."""
        self._not_ready("provider")

    def do_dataset(self, arg: str) -> None:
        """Show dataset info (Phase 5)."""
        self._not_ready("dataset")

    def do_artifacts(self, arg: str) -> None:
        """Browse saved artifacts (Phase 5)."""
        self._not_ready("artifacts")

    def _not_ready(self, name: str) -> None:
        self.console.print(
            f"[yellow]Command '{name}' is not implemented yet — coming in a later phase.[/yellow]"
        )