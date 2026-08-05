"""BaseCommand: shared helpers for all shell command handlers."""

from __future__ import annotations

from typing import Any

from rich.console import Console


class BaseCommand:
    """Base class for shell commands.

    Every command receives ``(config, console)`` via the constructor so it
    stays unit-testable (mock config + console).
    """

    def __init__(self, config: dict, console: Console):
        self.config = config
        self.console = console

    def execute(self, args: str) -> None:
        raise NotImplementedError

    @staticmethod
    def parse_args(args: str) -> dict:
        """Parse ``--key value --flag`` into a dict.

        Positional tokens are ignored; ``--flag`` with no value becomes True.
        """
        result: dict[str, Any] = {}
        tokens = args.split()
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t.startswith("--"):
                key = t[2:]
                if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                    result[key] = tokens[i + 1]
                    i += 2
                else:
                    result[key] = True
                    i += 1
            else:
                i += 1
        return result

    def error(self, message: str) -> None:
        self.console.print(f"[red]{message}[/red]")

    def success(self, message: str) -> None:
        self.console.print(f"[green]{message}[/green]")

    def info(self, message: str) -> None:
        self.console.print(f"[cyan]{message}[/cyan]")

    def warning(self, message: str) -> None:
        self.console.print(f"[yellow]{message}[/yellow]")