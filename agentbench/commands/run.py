"""``run`` command: execute an experiment run with live progress UI.

Wraps the existing ``agentbench.core.experiments.runner.run_experiments`` —
no core logic is rewritten. The command resolves parameters (flags -> config
-> defaults), asks for confirmation, wires an ``on_issue_complete`` callback
to a rich progress bar, then prints a summary.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from rich.panel import Panel
from rich.prompt import Confirm

from agentbench.commands.base import BaseCommand
from agentbench.ui.progress import create_experiment_progress

VALID_STRATEGIES = ("direct", "planning", "review")
DEFAULT_ISSUES = 50


class RunCommand(BaseCommand):
    """Handle ``run [--issues N] [--strategy S] [--output DIR] [--resume]``.

    ``provider_factory`` and ``issue_loader`` are injectable for tests; by
    default they wire the real core modules.
    """

    def __init__(
        self,
        config: dict,
        console=None,
        provider_factory: Callable[..., Any] | None = None,
        issue_loader: Callable[..., list] | None = None,
        interactive: bool = True,
    ):
        super().__init__(config, console)
        self._provider_factory = provider_factory
        self._issue_loader = issue_loader
        # When called from the TUI (interactive=False) we must not block on
        # Confirm prompts (stdin belongs to Textual) nor render the rich live
        # progress bar into a captured buffer (it floods the log stream).
        self._interactive = interactive

    # ------------------------------------------------------------------ #
    def execute(self, args: str) -> None:
        parsed = self.parse_args(args)

        try:
            issues = self._resolve_issues(parsed)
            strategy = self._resolve_strategy(parsed)
            output = self._resolve_output(parsed)
            resume = bool(parsed.get("resume", False))
        except ValueError as e:
            self.error(str(e))
            return

        # Refresh USD/IDR from a trusted API (BI JISDOR -> ECB -> fallback)
        self._refresh_rate()

        # Confirmation dialog (skipped when running under the TUI, where the
        # terminal stdin belongs to Textual and would block the worker).
        self.console.print(Panel.fit(
            f"[bold]Experiment Run[/bold]\n"
            f"  Issues: {issues}\n"
            f"  Strategy: {strategy}\n"
            f"  Output: {output}\n"
            f"  Mode: {'resume' if resume else 'fresh'}",
            border_style="cyan",
        ))
        if self._interactive and not Confirm.ask("Start experiment?", default=True):
            self.info("Aborted.")
            return

        # Wire provider + strategies from config
        try:
            provider, strategies = self._build_strategies(strategy)
        except Exception as e:  # noqa: BLE001 - provider init errors surface here
            self.error(f"Provider error: {e}")
            return

        if not provider.health_check():
            self.error("Provider health check failed — aborting.")
            return

        # Delegate to the existing runner with a progress callback
        from agentbench.core.dataset_loader import select_issues
        from agentbench.core.experiments.runner import run_experiments

        loader = self._issue_loader or select_issues
        issue_objs = loader()
        if issues:
            issue_objs = issue_objs[:issues]

        t0 = time.perf_counter()
        from agentbench.core.utils.logger import silence_console, restore_console

        silenced = silence_console()  # keep rich progress bar clean (1 bar, no loguru noise)

        def _simple_callback():
            """Line-based progress reporter for the TUI (no buffer flood)."""
            total = len(issue_objs) * len(strategies)
            done = {"n": 0}

            def on_issue_complete(
                instance_id: str,
                strategy: str,
                elapsed: float,
                tokens: int,
                cost_usd: float,
                success: bool,
                status: str,
            ) -> None:
                done["n"] += 1
                mark = "✓" if success else "✗"
                self.info(
                    f"  [{done['n']}/{total}] {mark} {strategy} on {instance_id} "
                    f"({elapsed:.1f}s, {tokens} tok) — {status}"
                )

            return on_issue_complete

        df = None
        exp_id = ""
        try:
            if not self._interactive:
                # TUI mode: no rich live progress (floods the captured log),
                # no stdin prompts; a simple per-issue line reporter instead.
                df, exp_id = run_experiments(
                    issue_objs,
                    strategies,
                    base_dir=output,
                    provider_name=self.config.get("provider", {}).get("name", "unknown"),
                    rate_limit_seconds=float(
                        self.config.get("experiment", {}).get("rate_limit", 1.5)
                    ),
                    resume=resume,
                    agents=self._agent_manifest(provider),
                    on_issue_complete=_simple_callback(),
                )
            else:
                with create_experiment_progress() as progress:
                    task = progress.add_task(
                        "Running experiment", total=len(issue_objs) * len(strategies)
                    )

                    def on_issue_complete(
                        instance_id: str,
                        strategy: str,
                        elapsed: float,
                        tokens: int,
                        cost_usd: float,
                        success: bool,
                        status: str,
                    ) -> None:
                        mark = "✓" if success else "✗"
                        progress.update(
                            task,
                            advance=1,
                            description=(
                                f"{mark} {strategy} on {instance_id} "
                                f"({elapsed:.1f}s, {tokens} tok, ${cost_usd:.4f})"
                            ),
                        )

                    df, exp_id = run_experiments(
                        issue_objs,
                        strategies,
                        base_dir=output,
                        provider_name=self.config.get("provider", {}).get("name", "unknown"),
                        rate_limit_seconds=float(
                            self.config.get("experiment", {}).get("rate_limit", 1.5)
                        ),
                        resume=resume,
                        agents=self._agent_manifest(provider),
                        on_issue_complete=on_issue_complete,
                    )
        except KeyboardInterrupt:
            if silenced:
                restore_console()
            if self._interactive and Confirm.ask(
                "\nAbort experiment? Partial results are saved.", default=False
            ):
                self.warning("Aborted — partial results saved in output dir.")
                return
            self.info("Continuing...")
            raise
        finally:
            if silenced:
                restore_console()

        elapsed_total = time.perf_counter() - t0
        self._save_config_snapshot(
            exp_id, output, len(issue_objs), list(strategies), provider
        )
        self._print_summary(df, exp_id, output, elapsed_total)

    def _save_config_snapshot(
        self,
        exp_id: str,
        output: str,
        issue_count: int,
        strategy_names: list[str],
        provider,
    ) -> None:
        """Write experiment.yaml into the run's output dir (reproducibility)."""
        try:
            from agentbench.core.experiments.experiment_config import (
                save_experiment_config,
            )

            exp_dir = f"{output}/{exp_id}" if exp_id else output
            path = save_experiment_config(
                exp_dir,
                researcher=self.config.get("researcher", {}),
                provider=self.config.get("provider", {}),
                experiment=self.config.get("experiment", {}),
                issue_count=issue_count,
                strategy_names=strategy_names,
                experiment_id=exp_id,
                agents=self._agent_manifest(provider),
            )
            self.info(f"  Config snapshot: {path}")
        except Exception as e:  # noqa: BLE001 - snapshot must not abort a run
            self.warning(f"Could not write experiment.yaml: {e}")

    # ------------------------------------------------------------------ #
    # Resolution helpers
    # ------------------------------------------------------------------ #
    def _resolve_issues(self, parsed: dict) -> int:
        raw = parsed.get("issues")
        if raw is None or raw is True:
            return DEFAULT_ISSUES
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid --issues value: '{raw}'. Use 1-50.") from None
        if not (1 <= n <= 50):
            raise ValueError("--issues must be between 1 and 50.")
        return n

    def _resolve_strategy(self, parsed: dict) -> str:
        raw = str(parsed.get("strategy", "all"))
        if raw == "all":
            return "all"
        if raw not in VALID_STRATEGIES:
            raise ValueError(
                f"Unknown strategy '{raw}'. Use: all|{'|'.join(VALID_STRATEGIES)}"
            )
        return raw

    @staticmethod
    def _resolve_default_output() -> str:
        """Default base output = 'results' (runner appends EXP-<id> itself)."""
        return "results"

    def _resolve_output(self, parsed: dict) -> str:
        raw = parsed.get("output")
        if raw and raw is not True:
            return str(raw)
        return self._resolve_default_output()

    def _refresh_rate(self) -> None:
        """Fetch a fresh USD/IDR rate from trusted APIs and persist it.

        Non-fatal: on failure the configured rate is kept and a warning is
        shown (a run must never be blocked by an offline rate service).
        """
        try:
            from agentbench.exchange_rate import fetch_usd_idr_rate

            rate, source, ts = fetch_usd_idr_rate()
            self.config.setdefault("experiment", {})["usd_idr_rate"] = rate
            try:
                from agentbench.config_manager import ConfigManager

                cm = ConfigManager()
                if cm.config_exists():
                    cfg = cm.load()
                    cfg.setdefault("experiment", {})["usd_idr_rate"] = rate
                    cm.save(cfg)
            except Exception:  # noqa: BLE001 - persistence is best-effort
                pass
            self.info(
                f"  USD/IDR: {rate:,.0f} (source: {source}, fetched {ts[:10]})"
            )
        except Exception as e:  # noqa: BLE001
            self.warning(
                f"  Could not fetch live USD/IDR rate ({e}); "
                "using configured rate."
            )

    # ------------------------------------------------------------------ #
    # Core wiring
    # ------------------------------------------------------------------ #
    def _build_strategies(self, strategy: str):
        """Instantiate provider + strategy objects from config.

        Returns (provider, strategies_dict). ``provider_factory`` is injectable
        for tests; default maps the config provider name to the core classes.
        """
        if self._provider_factory is not None:
            provider = self._provider_factory()
        else:
            provider = self._default_provider()

        from agentbench.core.strategies.direct_strategy import DirectStrategy
        from agentbench.core.strategies.planning_strategy import PlanningStrategy
        from agentbench.core.strategies.review_strategy import ReviewStrategy

        all_strategies = {
            "direct": DirectStrategy(provider),
            "planning": PlanningStrategy(provider),
            "review": ReviewStrategy(provider),
        }
        if strategy == "all":
            return provider, all_strategies
        return provider, {strategy: all_strategies[strategy]}

    def _default_provider(self):
        from agentbench.core.providers.gemini_provider import GeminiProvider
        from agentbench.core.providers.groq_provider import GroqProvider
        from agentbench.core.providers.opencode_provider import OpenCodeProvider
        from agentbench.core.providers.openrouter_provider import OpenRouterProvider

        name = self.config.get("provider", {}).get("name", "openrouter")
        mapping = {
            "gemini": GeminiProvider,
            "groq": GroqProvider,
            "openrouter": OpenRouterProvider,
            "opencode": OpenCodeProvider,
        }
        cls = mapping.get(name)
        if cls is None:
            raise ValueError(f"Unknown provider '{name}' in config.")
        return cls()

    def _agent_manifest(self, provider) -> list[dict[str, str]]:
        from agentbench.core.agents.registry import build_agent_team

        team = build_agent_team(provider)
        return [
            {"name": name, "prompt_file": getattr(agent, "prompt_file", "")}
            for name, agent in team.items()
        ]

    # ------------------------------------------------------------------ #
    def _print_summary(self, df, exp_id: str, output: str, elapsed: float) -> None:
        self.console.print()
        self.success(f"✓ Experiment completed: {exp_id}")
        self.info(f"  Output: {output}/{exp_id}/")
        self.info(f"  Elapsed: {elapsed:.1f}s")
        if df is not None and len(df):
            total_tokens = int(df["total_tokens"].sum()) if "total_tokens" in df else 0
            total_cost = float(df["cost_usd"].sum()) if "cost_usd" in df else 0.0
            success_rate = (
                float(df["success"].mean() * 100) if "success" in df else 0.0
            )
            self.info(
                f"  Tokens: {total_tokens:,} | Cost: ${total_cost:.4f} | "
                f"Success: {success_rate:.1f}%"
            )
            self.info("  View with: results summary")