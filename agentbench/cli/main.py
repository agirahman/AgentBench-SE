"""AgentBench-SE CLI entry dispatcher.

Usage:
    agentbench setup       -> run interactive setup wizard
    agentbench             -> launch interactive shell
    agentbench --version   -> print version
"""

from __future__ import annotations

import click

from agentbench.__version__ import __version__
from agentbench.config_manager import ConfigManager, ConfigError


@click.group(invoke_without_command=True)
@click.pass_context
@click.version_option(version=__version__, prog_name="AgentBench-SE")
def cli(ctx: click.Context) -> None:
    """AgentBench-SE: Framework for AI Agent Orchestration Strategy Evaluation."""
    if ctx.invoked_subcommand is None:
        launch_shell()


@cli.command()
def setup() -> None:
    """Run interactive setup wizard."""
    from agentbench.setup_wizard import run_setup

    run_setup()


def launch_shell() -> None:
    """Launch the interactive REPL, erroring if config is missing."""
    from agentbench.shell import AgentBenchShell

    cm = ConfigManager()
    try:
        cm.load()
    except ConfigError as e:
        click.secho(str(e), fg="red")
        return
    AgentBenchShell().cmdloop()


if __name__ == "__main__":
    cli()