"""Shell banner rendered with rich panels."""

from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel

from agentbench.__version__ import __version__


def display_banner(console: Console, config: dict) -> None:
    """Render the interactive shell banner + current-config summary."""
    researcher = config.get("researcher", {}) or {}
    provider = config.get("provider", {}) or {}

    name = researcher.get("name", "Unknown")
    prov = provider.get("name", "Unknown")
    model = provider.get("model", "Unknown")

    banner = Panel.fit(
        "[bold cyan]🧪 AgentBench-SE Interactive Shell[/bold cyan]\n"
        "\n"
        "Framework for AI Agent Orchestration Strategy Evaluation\n"
        f"Version {__version__} | Research by {name}",
        border_style="cyan",
    )
    console.print(banner)

    console.print("\n[bold]Current Configuration[/bold]")
    console.print(
        Panel(
            f"[bold]Provider:[/bold] {prov} ({model})\n"
            "[bold]Dataset:[/bold] SWE-bench Lite\n"
            "[bold]Strategies:[/bold] Direct | Planning | Planning+Review",
            border_style="dim",
            box=box.SIMPLE,
        )
    )
    console.print("\nType 'help' for available commands or 'exit' to quit.")