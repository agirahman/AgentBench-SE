# System Design Document (SDD)
## AgentBench-SE Interactive Shell

**Version:** 1.0  
**Date:** 2026-08-05  
**Author:** Agi Rahman Setiadi  
**Status:** Design Complete — Ready for Implementation

**Related Documents:**
- [PRD_INTERACTIVE_SHELL.md](./PRD_INTERACTIVE_SHELL.md) — Product Requirements

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Package Structure](#2-package-structure)
3. [Component Design](#3-component-design)
4. [Data Model](#4-data-model)
5. [Data Flow Diagrams](#5-data-flow-diagrams)
6. [Sequences](#6-sequences)
7. [Integration with Existing Code](#7-integration-with-existing-code)
8. [UI Components](#8-ui-components)
9. [Packaging](#9-packaging)
10. [Error Handling](#10-error-handling)
11. [Testing Strategy](#11-testing-strategy)
12. [Implementation Checklist](#12-implementation-checklist)

---

## 1. Architecture Overview

### 1.1 System Context

```
┌─────────────────────────────────────────────────────────────────┐
│                          USER                                    │
│              (Researcher / PhD Student)                          │
└────────────────────────┬────────────────────────────────────────┘
                         │ Terminal (stdin/stdout)
                         ▼
         ┌───────────────────────────────┐
         │   AgentBench CLI Entry        │
         │   - `agentbench setup`        │
         │   - `agentbench` (shell)      │
         └───────────────┬───────────────┘
                         │
         ┌───────────────▼───────────────┐
         │  Interactive Shell (REPL)      │
         │  - cmd.Cmd framework           │
         │  - Rich terminal UI            │
         └───────────────┬───────────────┘
                         │
         ┌───────────────▼───────────────┐
         │  Command Handlers              │
         │  - run, results, export, etc.  │
         └───────────────┬───────────────┘
                         │
         ┌───────────────▼───────────────┐
         │  Core Logic (Existing Code)    │
         │  - Providers, Strategies       │
         │  - Experiments, Evaluation     │
         └───────────────────────────────┘
```

### 1.2 Design Principles

| Principle | Description |
|-----------|-------------|
| **Non-breaking** | Existing `src/` code stays functional; new CLI wraps, not rewrites |
| **Separation of Concerns** | UI (rich) decoupled from logic (core) |
| **Thin Command Layer** | Commands are thin wrappers delegating to core modules |
| **Config-Driven** | All runtime settings from `~/.agentbench/config.yaml` |
| **Backward Compatible** | `python src/main.py` still works during transition |

---

## 2. Package Structure

```
agentbench-se/
├── agentbench/                     # Main package
│   ├── __init__.py                 # Package metadata
│   ├── __version__.py              # Version string (__version__ = "0.1.0")
│   │
│   ├── cli/                        # CLI entry points
│   │   ├── __init__.py
│   │   └── main.py                 # Entry dispatcher (click group)
│   │
│   ├── shell.py                    # Interactive shell (cmd.Cmd subclass)
│   ├── config_manager.py           # Config I/O (YAML)
│   ├── setup_wizard.py             # Interactive setup wizard
│   │
│   ├── commands/                   # Command implementations
│   │   ├── __init__.py
│   │   ├── base.py                 # BaseCommand (shared helpers)
│   │   ├── run.py                  # `run` command
│   │   ├── results.py              # `results` command
│   │   ├── export.py               # `export` command
│   │   ├── config.py               # `config` command
│   │   ├── provider.py             # `provider` command
│   │   ├── dataset.py              # `dataset` command
│   │   └── artifacts.py            # `artifacts` command
│   │
│   ├── ui/                         # UI components (rich-based)
│   │   ├── __init__.py
│   │   ├── banner.py               # Shell banner
│   │   ├── progress.py             # Progress bars & spinners
│   │   └── tables.py               # Result tables
│   │
│   └── core/                       # EXISTING src/ code (renamed)
│       ├── providers/
│       ├── strategies/
│       ├── agents/
│       ├── experiments/
│       ├── evaluation/
│       ├── models/
│       └── prompts/
│
├── setup.py                        # Packaging (entry_points)
├── pyproject.toml                  # Modern packaging (optional)
├── requirements.txt                # Dependencies (+ rich, click, pyyaml)
├── README.md                       # Updated installation & usage
└── tests/
    ├── test_config_manager.py
    ├── test_shell.py
    └── test_commands/
        ├── test_run.py
        └── test_results.py
```

## 3. Component Design

### 3.1 Entry Point — `cli/main.py`

**Responsibility:** Dispatch between `agentbench setup` and `agentbench` (shell).

**Dependencies:** `click`, `setup_wizard.run_setup`, `shell.AgentBenchShell`, `config_manager.ConfigManager`

**Module Design:**
```python
import click
from agentbench.setup_wizard import run_setup
from agentbench.shell import AgentBenchShell
from agentbench.config_manager import ConfigManager

@click.group(invoke_without_command=True)
@click.pass_context
@click.version_option(version="0.1.0", prog_name="AgentBench-SE")
def cli(ctx):
    """AgentBench-SE: Framework for AI Agent Orchestration Strategy Evaluation"""
    if ctx.invoked_subcommand is None:
        launch_shell()

@cli.command()
def setup():
    """Run interactive setup wizard"""
    run_setup()

def launch_shell():
    cm = ConfigManager()
    if not cm.config_exists():
        click.secho("No configuration found. Run 'agentbench setup' first.", fg="red")
        return
    AgentBenchShell().cmdloop()

if __name__ == "__main__":
    cli()
```

**setup.py entry point:**
```python
entry_points={
    "console_scripts": [
        "agentbench=agentbench.cli.main:cli",
    ],
}
```

---

### 3.2 ConfigManager — `config_manager.py`

**Responsibility:** Load/save/validate `~/.agentbench/config.yaml`; dot-notation get/set.

**Class:**
```
+----------------------------------------------+
| ConfigManager                                |
+----------------------------------------------+
| - config_dir: Path  (~/.agentbench)          |
| - config_path: Path (~/.agentbench/config.yaml)|
| - _config: dict | None   (cache)             |
+----------------------------------------------+
| + __init__()                                 |
| + config_exists() -> bool                    |
| + load() -> dict                             |
| + save(config: dict) -> None                 |
| + validate(config: dict) -> bool             |
| + reset() -> None                            |
| + get(key: str, default=None) -> Any         |
| + set(key: str, value: Any) -> None          |
+----------------------------------------------+
```

**Key Implementation:**
```python
import yaml
from pathlib import Path
from typing import Any

VALID_PROVIDERS = ["openrouter", "gemini", "groq", "opencode"]

class ConfigManager:
    def __init__(self):
        self.config_dir = Path.home() / ".agentbench"
        self.config_path = self.config_dir / "config.yaml"
        self.config_dir.mkdir(exist_ok=True)
        self._config = None

    def config_exists(self) -> bool:
        return self.config_path.exists()

    def load(self) -> dict:
        if self._config is not None:
            return self._config
        if not self.config_exists():
            return {}
        with open(self.config_path, "r") as f:
            self._config = yaml.safe_load(f) or {}
        return self._config

    def validate(self, config: dict) -> bool:
        for key in ("researcher", "provider", "experiment"):
            if key not in config:
                raise ValueError(f"Missing required section: {key}")
        p = config["provider"]
        if p.get("name") not in VALID_PROVIDERS:
            raise ValueError(f"provider.name must be one of {VALID_PROVIDERS}")
        if not p.get("api_key") and p["name"] != "opencode":
            raise ValueError("Missing provider.api_key")
        if not p.get("model"):
            raise ValueError("Missing provider.model")
        temp = config["experiment"].get("temperature", 0.2)
        if not (0.0 <= float(temp) <= 1.0):
            raise ValueError("experiment.temperature must be 0.0-1.0")
        return True

    def save(self, config: dict) -> None:
        self.validate(config)
        with open(self.config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        self._config = config

    def reset(self) -> None:
        if self.config_exists():
            self.config_path.unlink()
        self._config = None

    def get(self, key: str, default: Any = None) -> Any:
        cfg = self.load()
        cur = cfg
        for k in key.split("."):
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                return default
        return cur

    def set(self, key: str, value: Any) -> None:
        cfg = self.load()
        target = cfg
        parts = key.split(".")
        for k in parts[:-1]:
            target = target.setdefault(k, {})
        target[parts[-1]] = value
        self.save(cfg)
```

**Config Schema (`~/.agentbench/config.yaml`):**
```yaml
researcher:
  name: "Agi Rahman Setiadi"
  institution: "Universitas Negeri Jakarta"
  email: "agi.rahman.s@gmail.com"

provider:
  name: "openrouter"            # openrouter | gemini | groq | opencode
  api_key: "sk-or-v1-..."       # (opencode: may be empty, uses proxy)
  model: "tencent/hy3:free"

experiment:
  temperature: 0.2
  max_retries: 3
  rate_limit: 1.5
  usd_idr_rate: 16500.0
```

---

### 3.3 Setup Wizard — `setup_wizard.py`

**Responsibility:** Interactive wizard to collect config from user (first-time or re-run).

**Public API:**
```python
def run_setup() -> None:
    """Run interactive setup wizard (rich-based prompts)."""
```

**UI Flow:**
```
Banner (rich.panel, cyan border)
  -> Researcher Info (name, institution, email)
  -> Provider Selection (choices: openrouter, gemini, groq, opencode)
  -> Provider Config (API key [password=True], model [default per provider])
  -> Experiment Settings (temperature, max_retries, rate_limit, usd_idr_rate)
  -> Save via ConfigManager.save()
  -> Health check prompt (optional, spinner while testing)
  -> Success message
```

**Dependencies:** `rich.console.Console`, `rich.panel.Panel`, `rich.prompt.Prompt`, `rich.prompt.Confirm`, `config_manager.ConfigManager`

**Key Implementation:**
```python
import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from agentbench.config_manager import ConfigManager

console = Console()

PROVIDER_MODELS = {
    "openrouter": "tencent/hy3:free",
    "gemini": "gemini-1.5-flash",
    "groq": "llama-3.3-70b-versatile",
    "opencode": "deepseek/deepseek-chat",
}

def run_setup():
    console.print(Panel.fit(
        "[bold cyan]AgentBench-SE Setup Wizard[/bold cyan]\n"
        "Framework for AI Agent Orchestration Strategy Evaluation",
        border_style="cyan"))
    console.print("\nLet's configure your research environment.\n")

    config = {}

    console.print("[bold]Researcher Information[/bold]")
    console.print("-" * 60)
    config["researcher"] = {
        "name": Prompt.ask("Name"),
        "institution": Prompt.ask("Institution", default=""),
        "email": Prompt.ask("Email", default=""),
    }
    console.print()

    console.print("[bold]Provider Configuration[/bold]")
    console.print("-" * 60)
    provider_name = Prompt.ask(
        "Provider",
        choices=list(PROVIDER_MODELS.keys()),
        default="openrouter",
    )
    config["provider"] = {"name": provider_name}
    if provider_name != "opencode":
        config["provider"]["api_key"] = Prompt.ask("API Key", password=True)
    config["provider"]["model"] = Prompt.ask(
        "Model", default=PROVIDER_MODELS[provider_name])
    console.print()

    console.print("[bold]Experiment Settings[/bold]")
    console.print("-" * 60)
    config["experiment"] = {
        "temperature": float(Prompt.ask("Temperature (0.0-1.0)", default="0.2")),
        "max_retries": int(Prompt.ask("Max Retries", default="3")),
        "rate_limit": float(Prompt.ask("Rate Limit (seconds)", default="1.5")),
        "usd_idr_rate": float(Prompt.ask("USD to IDR Rate", default="16500.0")),
    }
    console.print()

    cm = ConfigManager()
    cm.save(config)
    console.print(f"[green]Configuration saved to {cm.config_path}[/green]")

    if Confirm.ask("\nTest provider connection?", default=True):
        console.print("Testing connection...")
        # TODO: provider health check integration
        console.print("[green]Connection successful![/green]")

    console.print("\n[bold green]Setup complete![/bold green] Run: [cyan]agentbench[/cyan]")
```
### 3.4 Interactive Shell — `shell.py`

**Responsibility:** Main REPL loop using `cmd.Cmd`; banner display; command dispatch.

**Class:**
```
+----------------------------------------------+
| AgentBenchShell (cmd.Cmd)                    |
+----------------------------------------------+
| - config_manager: ConfigManager              |
| - console: Console (rich)                    |
| - config: dict                               |
| prompt = "\\nagentbench> "                   |
+----------------------------------------------+
| + __init__()                                 |
| + preloop() -> None       (banner)           |
| + do_help(arg) -> None                       |
| + do_run(arg) -> None                        |
| + do_results(arg) -> None                    |
| + do_export(arg) -> None                     |
| + do_config(arg) -> None                     |
| + do_provider(arg) -> None                   |
| + do_dataset(arg) -> None                    |
| + do_artifacts(arg) -> None                  |
| + do_info(arg) -> None                       |
| + do_version(arg) -> None                    |
| + do_exit(arg) -> bool                       |
| + do_quit(arg) -> bool                       |
| + do_EOF(arg) -> bool                        |
| + emptyline() -> None                        |
+----------------------------------------------+
```

**Key Implementation:**
```python
import cmd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from agentbench.config_manager import ConfigManager
from agentbench.__version__ import __version__

class AgentBenchShell(cmd.Cmd):
    prompt = "\nagentbench> "

    def __init__(self):
        super().__init__()
        self.config_manager = ConfigManager()
        self.console = Console()
        self.config = self.config_manager.load()

    def preloop(self):
        name = self.config.get("researcher", {}).get("name", "Unknown")
        prov = self.config.get("provider", {}).get("name", "Unknown")
        model = self.config.get("provider", {}).get("model", "Unknown")

        banner = Panel.fit(
            f"[bold cyan]AgentBench-SE Interactive Shell[/bold cyan]\n\n"
            f"Framework for AI Agent Orchestration Strategy Evaluation\n"
            f"Version {__version__} | Research by {name}",
            border_style="cyan")
        self.console.print(banner)

        self.console.print("\n[bold]Current Configuration[/bold]")
        self.console.print("-" * 60)
        self.console.print(f"  Provider: {prov} ({model})")
        self.console.print(f"  Dataset: SWE-bench Lite (50 issues)")
        self.console.print(f"  Strategies: Direct | Planning | Planning+Review")
        self.console.print("\nType 'help' for available commands or 'exit' to quit.")

    def do_help(self, arg):
        """Show available commands or help for a specific command."""
        if arg:
            super().do_help(arg)
            return
        table = Table(title="Available Commands", show_header=False)
        table.add_column("Category", style="cyan")
        table.add_column("Commands")
        table.add_row("Experiment", "run, resume, status")
        table.add_row("Analysis", "results, compare, export")
        table.add_row("Configuration", "config, setup, provider")
        table.add_row("Data", "dataset, artifacts")
        table.add_row("Help", "help, info, version, exit")
        self.console.print(table)

    def do_run(self, arg):
        """Start experiment run.
        Usage: run [--issues N] [--strategy S] [--output DIR] [--resume]"""
        from agentbench.commands.run import RunCommand
        RunCommand(self.config, self.console).execute(arg)

    def do_results(self, arg):
        """View experiment results.
        Usage: results <summary|compare|errors|patch>"""
        from agentbench.commands.results import ResultsCommand
        ResultsCommand(self.config, self.console).execute(arg)

    def do_export(self, arg):
        """Export results to file.
        Usage: export [--format csv|json|markdown] [--output PATH]"""
        from agentbench.commands.export import ExportCommand
        ExportCommand(self.config, self.console).execute(arg)

    def do_config(self, arg):
        """Manage configuration.
        Usage: config <show|set <key> <value>|reset>"""
        from agentbench.commands.config import ConfigCommand
        ConfigCommand(self.config, self.console).execute(arg)

    def do_provider(self, arg):
        """Show/test provider connection.
        Usage: provider [--test]"""
        from agentbench.commands.provider import ProviderCommand
        ProviderCommand(self.config, self.console).execute(arg)

    def do_dataset(self, arg):
        """Show dataset info.
        Usage: dataset [--refresh]"""
        from agentbench.commands.dataset import DatasetCommand
        DatasetCommand(self.config, self.console).execute(arg)

    def do_artifacts(self, arg):
        """Browse saved artifacts.
        Usage: artifacts <issue_id> <strategy>"""
        from agentbench.commands.artifacts import ArtifactsCommand
        ArtifactsCommand(self.config, self.console).execute(arg)

    def do_info(self, arg):
        """Show framework information."""
        panel = Panel.fit(
            f"[bold]Framework:[/bold] AgentBench-SE v{__version__}\n"
            f"[bold]Researcher:[/bold] {self.config['researcher']['name']}\n"
            f"[bold]Institution:[/bold] {self.config['researcher'].get('institution', 'N/A')}\n\n"
            f"[bold]Paths:[/bold]\n"
            f"  Config: {self.config_manager.config_path}\n"
            f"  Results: ./results/\n"
            f"  Dataset: ~/.agentbench/cache/swe-bench-lite/\n",
            title="AgentBench-SE Information",
            border_style="cyan")
        self.console.print(panel)

    def do_version(self, arg):
        """Show version."""
        self.console.print(f"AgentBench-SE version {__version__}")

    def do_exit(self, arg):
        """Exit interactive shell."""
        self.console.print("\nGoodbye!")
        return True

    def do_quit(self, arg):
        """Exit interactive shell (alias)."""
        return self.do_exit(arg)

    def do_EOF(self, arg):
        """Handle Ctrl+D."""
        return self.do_exit(arg)

    def emptyline(self):
        """Do nothing on empty line."""
        pass
```

---

### 3.5 BaseCommand — `commands/base.py`

**Responsibility:** Shared helpers for all command handlers.

**Class:**
```python
class BaseCommand:
    def __init__(self, config: dict, console: Console):
        self.config = config
        self.console = console

    def execute(self, args: str) -> None:
        raise NotImplementedError

    def parse_args(self, args: str) -> dict:
        """Parse "--key value --flag" into dict."""
        result = {}
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
                    result[key] = True  # flag
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
```

**Command registration table (shell -> command class):**

| Shell method | Command class | Subcommands / Flags |
|--------------|---------------|---------------------|
| do_run | RunCommand | `--issues N`, `--strategy`, `--output`, `--resume` |
| do_results | ResultsCommand | `summary`, `compare`, `errors`, `patch <id> <strategy>` |
| do_export | ExportCommand | `--format csv/json/markdown`, `--output PATH` |
| do_config | ConfigCommand | `show`, `set <key> <value>`, `reset` |
| do_provider | ProviderCommand | `--test` (health check) |
| do_dataset | DatasetCommand | `--refresh` (re-download) |
| do_artifacts | ArtifactsCommand | `<issue_id> <strategy>` |
### 3.6 RunCommand — `commands/run.py`

**Responsibility:** Execute experiment run with live progress UI.

**Integration Point:** Existing `core/experiments/runner.py` (wrap, don't rewrite).

**Class:**
```python
class RunCommand(BaseCommand):
    def execute(self, args: str) -> None:
        parsed = self.parse_args(args)

        # Resolve values (flag -> config -> default)
        issues = int(parsed.get("issues", 50))
        strategy = parsed.get("strategy", "all")
        output = parsed.get("output", f"results/EXP-{timestamp()}")
        resume = parsed.get("resume", False)

        # Confirmation dialog
        self.console.print(Panel.fit(
            f"[bold]Experiment Run[/bold]\n"
            f"  Issues: {issues}\n"
            f"  Strategy: {strategy}\n"
            f"  Output: {output}\n"
            f"  Mode: {'resume' if resume else 'fresh'}",
            border_style="cyan"))
        if not Confirm.ask("Start experiment?", default=True):
            self.info("Aborted.")
            return

        # Delegate to existing runner with progress hooks
        from agentbench.core.experiments.runner import ExperimentRunner
        from agentbench.ui.progress import create_experiment_progress

        with create_experiment_progress() as progress:
            task = progress.add_task("Running...", total=issues)

            def on_issue_done(*_):
                progress.update(task, advance=1)

            runner = ExperimentRunner(
                provider=self.config["provider"]["name"],
                output_dir=output,
                resume=resume,
            )
            # If runner supports callbacks, wire them here.
            # Otherwise poll results/ for completed issues.
            runner.run(issues=issues, strategy=strategy,
                       on_issue_complete=on_issue_done)

        self.success(f"\nExperiment completed! Results: {output}/")
        self.info("View with: results summary")
```

**Progress UI (`ui/progress.py`):**
```python
from rich.progress import (Progress, SpinnerColumn, TextColumn,
                           BarColumn, TimeElapsedColumn)

def create_experiment_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
    )
```

**Note on integration:** If `ExperimentRunner` does not yet accept callbacks, Phase 3 includes a small non-breaking extension (optional constructor kwargs `on_issue_complete`, `on_strategy_complete`). If that is not feasible, RunCommand polls the output directory (count of completed issue folders) to update the bar.

---

### 3.7 ResultsCommand — `commands/results.py`

**Responsibility:** View experiment results: summary, compare, errors, patch.

**Integration Point:** Existing `core/view_results.py` (or direct CSV/statistics.json read).

**Class:**
```python
class ResultsCommand(BaseCommand):
    def execute(self, args: str) -> None:
        parts = args.split()
        sub = parts[0] if parts else "summary"

        if sub == "summary":
            self.show_summary()
        elif sub == "compare":
            self.show_compare()
        elif sub == "errors":
            self.show_errors()
        elif sub == "patch":
            self.show_patch(parts[1:])
        else:
            self.error(f"Unknown subcommand: {sub}. Use: summary|compare|errors|patch")

    def show_summary(self) -> None:
        # Load results (CSV / statistics.json)
        # Aggregate per strategy: issues, avg_time, avg_cost, success_rate
        # Render rich.table via ui/tables.py
        ...

    def show_compare(self) -> None:
        # Side-by-side comparison table
        ...

    def show_errors(self) -> None:
        # Rows where status != pass; show issue_id + error message
        ...

    def show_patch(self, parts: list) -> None:
        # parts: [issue_id, strategy]
        # Read artifacts/<issue>/<strategy>/patch.txt
        # Display with rich.syntax highlighting
        ...
```

**Summary table (`ui/tables.py`):**
```python
from rich.table import Table

def create_summary_table(rows: list[dict]) -> Table:
    table = Table(title="Results Summary")
    table.add_column("Strategy", style="cyan")
    table.add_column("Issues", justify="right")
    table.add_column("Avg Time", justify="right")
    table.add_column("Avg Cost", justify="right")
    table.add_column("Success", justify="right")
    for r in rows:
        table.add_row(r["strategy"], str(r["issues"]),
                      f"{r['avg_time']:.1f}s", f"${r['avg_cost']:.3f}",
                      f"{r['success_rate']}%")
    return table
```

---

### 3.8 ExportCommand — `commands/export.py`

**Responsibility:** Export results to CSV / JSON / Markdown.

**Class:**
```python
class ExportCommand(BaseCommand):
    def execute(self, args: str) -> None:
        parsed = self.parse_args(args)
        fmt = parsed.get("format", "csv")
        out = parsed.get("output", f"results/latest/export.{fmt}")

        # Load results -> rows
        # fmt == csv: csv.writer
        # fmt == json: json.dump
        # fmt == markdown: table rendering
        self.success(f"Exported to {out}")
```

**Export formats:**

| Format | Structure |
|--------|-----------|
| CSV | One row per (issue, strategy): issue_id, strategy, status, time, tokens_in, tokens_out, cost_usd, cost_idr |
| JSON | `{"experiment": "...", "issues": [...], "strategies": {...}}` |
| Markdown | Human-readable table (suitable for thesis appendix) |
### 3.9 ConfigCommand — `commands/config.py`

**Responsibility:** Show/edit/reset configuration.

**Class:**
```python
class ConfigCommand(BaseCommand):
    def execute(self, args: str) -> None:
        parts = args.split()
        sub = parts[0] if parts else "show"

        if sub == "show":
            self.show_config()
        elif sub == "set":
            self.set_config(parts[1:])
        elif sub == "reset":
            self.reset_config()
        else:
            self.error(f"Unknown subcommand: {sub}. Use: show|set|reset")

    def show_config(self) -> None:
        from agentbench.config_manager import ConfigManager
        cm = ConfigManager()
        cfg = cm.load()
        if not cfg:
            self.error("No configuration found. Run setup first.")
            return
        # Mask API keys: show first 8 chars + "***"
        prov = cfg.get("provider", {})
        if "api_key" in prov and prov["api_key"]:
            prov["api_key"] = prov["api_key"][:8] + "***"
        # Pretty-print as YAML
        import yaml
        self.console.print(yaml.dump(cfg, default_flow_style=False, sort_keys=False))

    def set_config(self, parts: list) -> None:
        if len(parts) != 2:
            self.error("Usage: config set <key> <value>  (e.g. provider.model llama-3.3-70b)")
            return
        key, value = parts
        cm = ConfigManager()
        # Type coercion: int/float if applicable
        cm.set(key, value)
        self.success(f"Set {key} = {value}")

    def reset_config(self) -> None:
        if Confirm.ask("This will delete current configuration. Continue?", default=False):
            ConfigManager().reset()
            self.success("Configuration reset. Run 'setup' to reconfigure.")
```

---

### 3.10 ProviderCommand — `commands/provider.py`

**Responsibility:** Show provider info and test connection.

**Class:**
```python
class ProviderCommand(BaseCommand):
    def execute(self, args: str) -> None:
        parsed = self.parse_args(args)
        prov = self.config.get("provider", {})
        name = prov.get("name", "?")
        model = prov.get("model", "?")

        if "--test" in args:
            with Progress(SpinnerColumn(), TextColumn("Testing connection...")) as p:
                p.add_task("test")
                ok, msg = self._health_check(name, prov)
            if ok:
                self.success(f"Provider {name} ({model}) online")
            else:
                self.error(f"Provider error: {msg}")
        else:
            self.info(f"Provider: {name} | Model: {model}")

    def _health_check(self, name: str, prov: dict) -> tuple[bool, str]:
        # Send minimal prompt ("Reply with only: OK") via existing provider client
        # Return (True, "") on success, (False, error_message) on failure
        from agentbench.core.providers import create_client  # existing factory
        try:
            client = create_client(name, api_key=prov.get("api_key"),
                                   model=prov.get("model"))
            resp = client.chat("Reply with only: OK", max_tokens=5)
            return True, ""
        except Exception as e:
            return False, str(e)
```

---

### 3.11 DatasetCommand — `commands/dataset.py`

**Responsibility:** Show SWE-bench Lite dataset info (cached) or refresh.

**Class:**
```python
class DatasetCommand(BaseCommand):
    def execute(self, args: str) -> None:
        parsed = self.parse_args(args)
        refresh = "--refresh" in args

        # Load dataset info (cached at ~/.agentbench/cache/swe-bench-lite/)
        # If refresh: re-download from HuggingFace
        # Show repo breakdown table:
        #   Django: N, SymPy: N, Matplotlib: N, scikit-learn: N, Requests: N, Seaborn: N
        # Show difficulty distribution: easy/medium/hard
        ...
```

---

### 3.12 ArtifactsCommand — `commands/artifacts.py`

**Responsibility:** Display saved artifacts (planner.md, executor.md, reviewer.md, patch.txt).

**Class:**
```python
class ArtifactsCommand(BaseCommand):
    def execute(self, args: str) -> None:
        parts = args.split()
        if len(parts) != 2:
            self.error("Usage: artifacts <issue_id> <strategy>")
            return
        issue_id, strategy = parts

        base = Path("results") / "latest" / "artifacts" / issue_id / strategy
        if not base.exists():
            self.error(f"No artifacts for {issue_id} / {strategy}")
            return

        for f in sorted(base.iterdir()):
            if f.suffix in (".md", ".txt", ".patch"):
                self.console.print(f"\n[bold cyan]=== {f.name} ===[/bold cyan]")
                if f.suffix == ".md":
                    from rich.markdown import Markdown
                    self.console.print(Markdown(f.read_text()))
                else:
                    from rich.syntax import Syntax
                    self.console.print(Syntax(f.read_text(), "diff" if f.suffix == ".patch" else "text"))
```

---

## 4. Data Model

### 4.1 Config (`~/.agentbench/config.yaml`)

See Section 3.2 schema. Three top-level sections: `researcher`, `provider`, `experiment`.

### 4.2 Runtime Objects

| Object | Source | Description |
|--------|--------|-------------|
| `config: dict` | ConfigManager.load() | Merged config passed to every command |
| `run_params: dict` | RunCommand.parse_args() | issues, strategy, output, resume |
| `result_rows: list[dict]` | results.py loader | Per (issue, strategy) metric rows |
| `console: Console` | rich | Shared output handle injected into commands |

### 4.3 Result File Layout (existing, unchanged)

```
results/
└── EXP-<timestamp>/
    ├── experiment.yaml        # config snapshot used for the run
    ├── results.csv            # per-(issue, strategy) metrics
    ├── statistics.json        # aggregate stats
    ├── predictions/
    │   └── predictions.jsonl  # patch predictions
    └── artifacts/
        └── <issue_id>/
            ├── planner.md
            ├── executor.md
            ├── reviewer.md
            └── patch.txt
```

---

## 5. Data Flow Diagrams

### 5.1 Setup Flow

```
agentbench setup
  -> cli.main.setup()
  -> setup_wizard.run_setup()
  -> collect researcher info
  -> collect provider config
  -> collect experiment settings
  -> ConfigManager.validate()
  -> ConfigManager.save() -> ~/.agentbench/config.yaml
  -> (optional) provider health check
  -> success message
```

### 5.2 Shell Launch Flow

```
agentbench
  -> cli.main.launch_shell()
  -> ConfigManager.config_exists()?
       No  -> error: "Run 'agentbench setup' first"
       Yes -> AgentBenchShell().cmdloop()
                -> preloop(): banner + config summary
                -> wait for user input
```

### 5.3 Run Command Flow

```
agentbench> run --issues 10
  -> do_run("--issues 10")
  -> RunCommand.execute()
  -> parse args
  -> confirmation dialog (Y/n)
  -> ExperimentRunner (existing) with progress hooks
  -> for each issue -> for each strategy -> run -> update progress
  -> summary: time, cost, success rate
```

### 5.4 Results Command Flow

```
agentbench> results summary
  -> do_results("summary")
  -> ResultsCommand.execute()
  -> load results (CSV/statistics.json)
  -> aggregate per strategy
  -> render rich.table
```
## 6. Integration with Existing Code

### 6.1 Strategy

**Goal:** Keep existing `src/` code untouched as much as possible.

| Step | Action | Breaking? |
|------|--------|-----------|
| 1 | Rename `src/` -> `agentbench/core/` (update all internal imports) | Low risk, do with sed + test |
| 2 | Add `agentbench/__init__.py`, `__version__.py` | No |
| 3 | Create `cli/`, `commands/`, `ui/`, `config_manager.py`, `setup_wizard.py`, `shell.py` | No (new files) |
| 4 | Add optional callbacks to `core/experiments/runner.py` (`on_issue_complete`, `on_strategy_complete`) | No (optional kwargs) |
| 5 | Wrap existing `view_results.py` logic in `commands/results.py` | No |
| 6 | Update `requirements.txt`: add `rich>=13.0`, `click>=8.1`, `pyyaml>=6.0` | No |
| 7 | Create `setup.py` with entry_points | No |

### 6.2 Backward Compatibility

- `python src/main.py --provider openrouter --issues 10` still works during transition
- `.env` file (if present) is read by existing core code; new config file adds a second source
- Optional: on first `agentbench setup`, auto-migrate `.env` values into YAML (nice-to-have, not required)

### 6.3 Dependency Injection

Every command receives `(config: dict, console: Console)` via constructor:
```python
RunCommand(self.config, self.console).execute(arg)
```
This keeps commands unit-testable (mock config + console).

---

## 7. UI Components

### 7.1 Banner (`ui/banner.py`)

```python
from rich.console import Console
from rich.panel import Panel

def display_banner(config: dict) -> None:
    name = config["researcher"]["name"]
    prov = config["provider"]["name"]
    model = config["provider"]["model"]
    console = Console()
    console.print(Panel.fit(
        f"[bold cyan]AgentBench-SE Interactive Shell[/bold cyan]\n\n"
        f"Framework for AI Agent Orchestration Strategy Evaluation\n"
        f"Version 0.1.0 | Research by {name}",
        border_style="cyan"))
    console.print("\n[bold]Current Configuration[/bold]")
    console.print("-" * 60)
    console.print(f"  Provider: {prov} ({model})")
    console.print(f"  Dataset: SWE-bench Lite (50 issues)")
    console.print(f"  Strategies: Direct | Planning | Planning+Review")
```

### 7.2 Color Conventions

| Purpose | Color |
|---------|-------|
| Success | green |
| Error | red |
| Info | cyan |
| Warning | yellow |
| Prompts | white/default |

### 7.3 Progress (`ui/progress.py`)

- `create_experiment_progress()` — Progress with spinner, bar, percentage, elapsed time
- Spinner during active inference: `SpinnerColumn()`

---

## 8. Packaging

```python
# setup.py
from setuptools import setup, find_packages

setup(
    name="agentbench-se",
    version="0.1.0",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        "click>=8.1.0",
        "rich>=13.0.0",
        "pyyaml>=6.0.0",
        # existing deps (openai, google-genai, datasets, pandas, loguru, python-dotenv)
    ],
    entry_points={
        "console_scripts": [
            "agentbench=agentbench.cli.main:cli",
        ],
    },
    python_requires=">=3.10",
    author="Agi Rahman Setiadi",
    description="Framework for evaluating AI agent orchestration strategies",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
)
```

**Install (development):**
```bash
pip install -e .
```

---

## 9. Error Handling

| Scenario | Handling |
|----------|----------|
| No config file | `agentbench` -> red error + hint "Run 'agentbench setup'" |
| Corrupted YAML | ConfigManager.load() -> try/except yaml.YAMLError -> error + hint to delete config |
| Invalid config value | ConfigManager.validate() -> ValueError with specific message |
| Unknown command | cmd.Cmd built-in: "*** Unknown syntax: xyz" (customize via `default()` method) |
| Invalid flag value | parse_args + per-command validation with actionable message |
| Provider offline | ProviderCommand health check -> red error with underlying message |
| Experiment crash | Runner already logs errors; shell continues, user can `resume` |
| CTRL+C during run | Confirm "Abort experiment? (Y/n)"; on yes -> save partial results, return to shell |
| CTRL+C at prompt | cmd.Cmd handles KeyboardInterrupt gracefully (emptyline) |

---

## 10. Testing Strategy

### 10.1 Unit Tests

| File | Tests |
|------|-------|
| `tests/test_config_manager.py` | save/load roundtrip, validate (valid + invalid), get/set dot-notation, reset |
| `tests/test_shell.py` | instantiation, preloop banner, do_help, do_version, do_exit returns True |
| `tests/test_commands/test_run.py` | parse_args, confirmation flow (mock Confirm), runner invocation (mock) |
| `tests/test_commands/test_results.py` | summary aggregation from fixture CSV, error subcommand |
| `tests/test_setup_wizard.py` | run_setup with mocked prompts produces valid config |

### 10.2 Integration Tests

- `pip install -e .` -> `agentbench --version` returns 0.1.0
- `agentbench setup` with scripted answers creates valid `~/.agentbench/config.yaml`
- `agentbench` enters shell, `help` prints table, `exit` returns
- `run --issues 1` against mock provider completes and writes results

### 10.3 Manual QA Checklist (UAT)

```
[ ] Setup wizard runs end-to-end with clear prompts
[ ] Banner shows researcher name + provider + model
[ ] help shows categorized command list
[ ] run --issues 1 completes with progress bar
[ ] results summary shows strategy table
[ ] export --format csv creates file
[ ] config set provider.model <x> persists
[ ] provider --test handles offline gracefully
[ ] exit/quit/Ctrl+D all work
[ ] Unknown command shows friendly error
```

---

## 11. Implementation Checklist

### Phase 1: Setup & Packaging (2-3h)
- [x] `pip install rich click pyyaml`
- [x] Rename `src/` -> `agentbench/core/` (update imports) — *git mv + rewrite imports; shim `src/main.py` & `src/view_results.py` keep legacy entry points working; 81/81 tests pass*
- [x] Create `agentbench/__init__.py`, `__version__.py`
- [x] Create `setup.py` (entry_points)
- [x] Create `agentbench/cli/main.py`
- [x] Create `agentbench/config_manager.py`
- [x] Create `agentbench/setup_wizard.py`
- [x] Test: `pip install -e .` && `agentbench setup` -> config created

### Phase 2: Shell Foundation (2-3h)
- [x] Create `agentbench/shell.py` (AgentBenchShell)
- [x] preloop banner, do_help, do_exit, do_info, do_version
- [x] Create `agentbench/ui/banner.py`
- [x] Test: `agentbench` -> banner -> help -> exit

### Phase 3: Run Command (3-4h)
- [x] Create `agentbench/commands/base.py` *(created early to support `config` command)*
- [x] Create `agentbench/commands/run.py`
- [x] Add callbacks to `core/experiments/runner.py` (optional kwargs) — *`on_issue_complete` wired on success + TIMEOUT paths*
- [x] Create `agentbench/ui/progress.py`
- [x] Confirmation dialog + progress bar
- [x] Test: `run --issues 5` full experiment with live progress

### Phase 4: Analysis Commands (2-3h)
- [x] Create `agentbench/commands/results.py` (summary/compare/errors/patch)
- [x] Create `agentbench/ui/tables.py`
- [x] Create `agentbench/commands/export.py` (csv/json/markdown)
- [x] Test: `results summary` shows table

### Phase 5: Data Management (1-2h)
- [x] Create `agentbench/commands/config.py`
- [x] Create `agentbench/commands/provider.py`
- [x] Create `agentbench/commands/dataset.py`
- [x] Create `agentbench/commands/artifacts.py`
- [x] Test: `config show`, `provider --test`, `dataset`

### Phase 6: Polish & Documentation (2-3h)
- [x] Update `README.md` (installation & usage)
- [x] Command help strings (`help <command>`)
- [x] Input validation + friendly errors
- [x] Full workflow test: setup -> run -> results -> export
- [ ] Optional: demo GIF *(deferred)*

**Total: 12-18 hours**

---

## 12. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Breaking existing code during `src/` rename | High | Keep git history; test after rename; backward-compat shim |
| Config migration issues | Medium | Auto-convert `.env` on first setup (nice-to-have) |
| rich version conflicts | Low | Pin `rich>=13.0`; test on Linux/WSL |
| Runner lacks callback hooks | Medium | Fallback: poll results dir for progress |
| Scope creep | Medium | Strict Phase 1-6; defer nice-to-haves to future work |

---

**End of SDD — Ready for Implementation** 🚀
