"""Interactive setup wizard for AgentBench-SE.

Guides a first-time user through creating ``~/.agentbench/config.yaml``:
researcher info, provider selection, API key (masked), model, experiment
settings, then an optional connection test.

Design: uses ``rich`` prompts and renders a success panel at the end.
Re-running ``agentbench setup`` overwrites an existing config.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt

from agentbench.config_manager import ConfigManager, ConfigError

DEFAULT_MODELS = {
    "openrouter": "deepseek/deepseek-v4-flash",
    "gemini": "gemini-2.0-flash",
    "groq": "llama-3.3-70b-versatile",
    "opencode": "deepseek-v4-flash",
}

VALID_TEMPERATURE = (0.0, 1.0)


def run_setup() -> None:
    """Run the interactive setup wizard end-to-end."""
    console = Console()
    cm = ConfigManager()

    console.print(Panel.fit(
        "[bold cyan]AgentBench-SE Setup Wizard[/bold cyan]\n"
        "Let's configure your researcher profile and LLM provider.",
        border_style="cyan",
    ))

    # 1. Researcher info
    console.print("\n[bold]Researcher Information[/bold]")
    name = Prompt.ask("[cyan]✎ Name[/cyan]", default="Agi Rahman Setiadi")
    institution = Prompt.ask("[cyan]Institution[/cyan]", default="Universitas Negeri Jakarta")
    email = Prompt.ask("[cyan]E-mail[/cyan]", default="")

    # 2. Provider selection
    console.print("\n[bold]LLM Provider[/bold]")
    provider = Prompt.ask(
        "[cyan]Choose provider[/cyan]",
        choices=list(DEFAULT_MODELS.keys()),
        default="openrouter",
    )

    # 3. API key (masked)
    # NOTE: getpass.getpass on Windows reads from the console via msvcrt and
    # HANGS forever when stdin is a pipe (tests/CI). Only mask when stdin is
    # an interactive tty; otherwise fall back to a plain readline prompt.
    import sys as _sys

    api_key = Prompt.ask(
        f"[cyan]API key for {provider}[/cyan]",
        password=_sys.stdin.isatty(),
        show_default=True,
    )

    # 4. Model
    model_default = DEFAULT_MODELS[provider]
    model = Prompt.ask("[cyan]Model[/cyan]", default=model_default) or model_default

    # 5. Experiment settings
    console.print("\n[bold]Experiment Settings[/bold]")
    temperature = FloatPrompt.ask("[cyan]Temperature (0.0-1.0)[/cyan]", default=0.2)
    max_retries = IntPrompt.ask("[cyan]Max retries[/cyan]", default=3)
    rate_limit = FloatPrompt.ask("[cyan]Rate limit (s)[/cyan]", default=1.5)

    # USD/IDR: prefer a live rate from trusted APIs (BI JISDOR -> ECB -> fallback)
    usd_idr_default = 16500.0
    usd_idr_source = "default"
    try:
        from agentbench.exchange_rate import fetch_usd_idr_rate

        usd_idr_default, usd_idr_source, _ = fetch_usd_idr_rate()
        console.print(
            f"[cyan]Live USD/IDR rate: {usd_idr_default:,.0f} "
            f"(source: {usd_idr_source})[/cyan]"
        )
    except Exception as e:  # noqa: BLE001
        console.print(
            f"[yellow]Could not fetch live USD/IDR rate ({e}); "
            "using default.[/yellow]"
        )
    usd_idr_rate = FloatPrompt.ask(
        "[cyan]USD/IDR rate[/cyan]", default=usd_idr_default
    )

    config = {
        "researcher": {
            "name": name or "Unknown",
            "institution": institution,
            "email": email,
        },
        "provider": {
            "name": provider,
            "api_key": api_key,
            "model": model,
        },
        "experiment": {
            "temperature": temperature,
            "max_retries": max_retries,
            "rate_limit": rate_limit,
            "usd_idr_rate": usd_idr_rate,
        },
    }

    # Validate locally before any live check
    try:
        cm.validate(config, require_api_key=True)
    except ConfigError as e:
        console.print(f"[red]✗ Invalid configuration: {e}[/red]")
        return

    # 6. Provider health check (optional, non-blocking)
    if Confirm.ask("\nRun a provider connection test?", default=True):
        console.print("[cyan]Testing connection...[/cyan]")
        from agentbench.provider_health import check_connection

        ok, message = check_connection(provider, api_key, model)
        if ok:
            console.print(f"[green]✓ Connection successful. {message}[/green]")
        else:
            console.print(f"[yellow]⚠ Provider test failed: {message}[/yellow]")
            if Confirm.ask("Continue anyway?", default=False):
                console.print("[yellow]Proceeding with best-effort config.[/yellow]")
            else:
                console.print("[red]Setup aborted. You can re-run 'agentbench setup'.[/red]")
                return

    cm.save(config)
    console.print()
    console.print(Panel.fit(
        "[bold green]✓ Setup complete![/bold green]\n"
        "Run [cyan]agentbench[/cyan] to launch the interactive shell.",
        border_style="green",
    ))