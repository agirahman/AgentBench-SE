"""Setup screen: experiment configuration form (PRD §7.1, Patch 3).

Interactive form binding to ``BenchmarkState`` (Patch 2):

- Provider dropdown + model input (prefilled from config / core defaults)
- Numeric inputs with inline real-time validation (temperature range
  mirrors ``ConfigManager.validate``, retries, rate limit, concurrency)
- Task checklist (Select All / None) built from the backend's default repos
- Auto-generated timestamped output dir (editable)
- ``Start Experiment`` persists the config via ``state.apply_config`` and
  emits ``setup.submitted`` with the per-run parameters (tasks, concurrency,
  output dir). ``Load Profile`` re-reads the config file into the form.
"""

from __future__ import annotations

import re
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Input, Select, Static

from agentbench.core.experiments.experiment_config import DEFAULT_REPOS
from agentbench.tui.state import BenchmarkState
from agentbench.tui.widgets.shell import ShellScreen

# Provider options: (value, label) — values must match ConfigManager.validate.
PROVIDERS: list[tuple[str, str]] = [
    ("openrouter", "OpenRouter"),
    ("gemini", "Gemini"),
    ("groq", "Groq"),
    ("opencode", "OpenCode"),
]

# Validation ranges. Temperature mirrors ConfigManager.validate (0.0-1.0);
# the rest are form-level constraints from PRD §7.1.
TEMP_MIN, TEMP_MAX = 0.0, 1.0
RETRIES_MIN, RETRIES_MAX = 1, 10
RATE_MIN, RATE_MAX = 0.1, 10.0
CONCURRENCY_MIN, CONCURRENCY_MAX = 1, 8
OUTDIR_RE = re.compile(r"^[\w./\\:\-]+$")

DEFAULT_TASKS: list[str] = list(DEFAULT_REPOS.keys())

# Checkbox ids must be CSS-safe ("/" is not), so map repo -> id and back.
TASK_IDS: dict[str, str] = {
    repo: f"setup-task-{repo.replace('/', '_')}" for repo in DEFAULT_TASKS
}
REPO_FROM_ID: dict[str, str] = {cid: repo for repo, cid in TASK_IDS.items()}


def _default_model_for(provider: str) -> str:
    """Default model for a provider, from the core env-backed Config."""
    from agentbench.core.config import Config

    return {
        "openrouter": Config.OPENROUTER_MODEL,
        "gemini": Config.GEMINI_MODEL,
        "groq": Config.GROQ_MODEL,
        "opencode": Config.OPENCODE_MODEL,
    }.get(provider, "")


def auto_output_dir(now: datetime | None = None) -> str:
    """Timestamped default output dir: ``./results/run_YYYYMMDD_HHMM``."""
    ts = (now or datetime.now()).strftime("%Y%m%d_%H%M")
    return f"./results/run_{ts}"


def _f(value: str, lo: float, hi: float) -> float:
    """Parse a float field; raises ValueError when out of range."""
    parsed = float(value.strip())
    if not (lo <= parsed <= hi):
        raise ValueError(f"must be in {lo:g}-{hi:g}")
    return parsed


def _i(value: str, lo: int, hi: int) -> int:
    parsed = int(value.strip())
    if not (lo <= parsed <= hi):
        raise ValueError(f"must be an integer in {lo}-{hi}")
    return parsed


class SetupScreen(ShellScreen):
    """Experiment configuration form."""

    nav_key = "setup"
    footer_hint = "Setup — isi form lalu Start · Select All/None untuk tasks · Ctrl+Q quit"

    @property
    def _state(self) -> BenchmarkState:
        """The app's observable state (typed helper)."""
        return self.app.state  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ #
    def body(self):
        cfg = self._state.config or {}
        provider = str(cfg.get("provider", {}).get("name", "openrouter"))
        if provider not in dict(PROVIDERS):
            provider = "openrouter"
        model = str(cfg.get("provider", {}).get("model", "")) or _default_model_for(provider)
        experiment = cfg.get("experiment", {}) or {}
        researcher = cfg.get("researcher", {}) or {}

        yield Static("[b accent]Experiment Configuration[/b accent]", id="setup-title")
        with Vertical(id="setup-form"):
            with Horizontal(classes="field-row"):
                yield Static("Provider", classes="field-label")
                yield Select(
                    [(label, value) for value, label in PROVIDERS],
                    value=provider,
                    id="setup-provider",
                )
            with Horizontal(classes="field-row"):
                yield Static("Model", classes="field-label")
                yield Input(model, id="setup-model", placeholder="provider/model or model name")
            with Horizontal(classes="field-row"):
                yield Static("Temperature", classes="field-label")
                yield Input(str(experiment.get("temperature", 0.2)), id="setup-temp")
                yield Static("", classes="field-err", id="setup-temp-err")
            with Horizontal(classes="field-row"):
                yield Static("Max retries", classes="field-label")
                yield Input(str(experiment.get("max_retries", 3)), id="setup-retries")
                yield Static("", classes="field-err", id="setup-retries-err")
            with Horizontal(classes="field-row"):
                yield Static("Rate limit", classes="field-label")
                yield Input(str(experiment.get("rate_limit", 1.5)), id="setup-rate")
                yield Static("", classes="field-err", id="setup-rate-err")
            with Horizontal(classes="field-row"):
                yield Static("Researcher", classes="field-label")
                yield Input(
                    str(researcher.get("name", "")),
                    id="setup-researcher",
                    placeholder="Nama peneliti (wajib)",
                )

            yield Static("Tasks", classes="field-label")
            with Horizontal(id="setup-task-actions"):
                yield Button("Select All", id="setup-select-all", classes="small")
                yield Button("None", id="setup-select-none", classes="small")
            with VerticalScroll(id="setup-tasks", classes="checklist"):
                selected = set(
                    cfg.get("dataset", {}).get("repos", DEFAULT_TASKS)
                ) or set(DEFAULT_TASKS)
                for repo in DEFAULT_TASKS:
                    yield Checkbox(repo, value=repo in selected, id=TASK_IDS[repo])

            with Horizontal(classes="field-row"):
                yield Static("Concurrency", classes="field-label")
                yield Input("1", id="setup-concurrency")
                yield Static("", classes="field-err", id="setup-concurrency-err")
            with Horizontal(classes="field-row"):
                yield Static("Output dir", classes="field-label")
                yield Input(auto_output_dir(), id="setup-outdir")
                yield Static("", classes="field-err", id="setup-outdir-err")

            with Horizontal(id="setup-actions"):
                yield Button("Start Experiment", id="setup-start", classes="primary")
                yield Button("Load Profile", id="setup-load")

        yield Static("", id="setup-status")

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def _field_errors(self) -> dict[str, str]:
        """Run real-time validation over the form; return {field: error}."""
        errors: dict[str, str] = {}
        model = self.query_one("#setup-model", Input).value.strip()
        researcher = self.query_one("#setup-researcher", Input).value.strip()

        try:
            _f(self.query_one("#setup-temp", Input).value, TEMP_MIN, TEMP_MAX)
        except ValueError as e:
            errors["temp"] = str(e)
        try:
            _i(self.query_one("#setup-retries", Input).value, RETRIES_MIN, RETRIES_MAX)
        except ValueError:
            errors["retries"] = f"must be an integer in {RETRIES_MIN}-{RETRIES_MAX}"
        try:
            _f(self.query_one("#setup-rate", Input).value, RATE_MIN, RATE_MAX)
        except ValueError as e:
            errors["rate"] = str(e)
        try:
            _i(self.query_one("#setup-concurrency", Input).value,
               CONCURRENCY_MIN, CONCURRENCY_MAX)
        except ValueError:
            errors["concurrency"] = f"must be an integer in {CONCURRENCY_MIN}-{CONCURRENCY_MAX}"

        outdir = self.query_one("#setup-outdir", Input).value.strip()
        if not outdir or not OUTDIR_RE.match(outdir):
            errors["outdir"] = "invalid path"
        if not model:
            errors["model"] = "model name is required"
        if not researcher:
            errors["researcher"] = "researcher name is required"
        return errors

    def _show_field_errors(self, errors: dict[str, str]) -> None:
        for field in ("temp", "retries", "rate", "concurrency", "outdir"):
            widget = self.query_one(f"#setup-{field}-err", Static)
            widget.update(errors.get(field, ""))
            widget.set_class(bool(errors.get(field)), "visible")

    def _collect(self) -> dict:
        """Collect validated values as a config-shaped dict (per-run too)."""
        selected = [
            REPO_FROM_ID.get(c.id, c.id)  # type: ignore[attr-defined]
            for c in self.query("#setup-tasks Checkbox")
            if getattr(c, "value", False)
        ]
        return {
            "config_updates": {
                "researcher": {"name": self.query_one("#setup-researcher", Input).value.strip()},
                "provider": {
                    "name": self.query_one("#setup-provider", Select).value,
                    "model": self.query_one("#setup-model", Input).value.strip(),
                },
                "experiment": {
                    "temperature": _f(self.query_one("#setup-temp", Input).value,
                                      TEMP_MIN, TEMP_MAX),
                    "max_retries": _i(self.query_one("#setup-retries", Input).value,
                                      RETRIES_MIN, RETRIES_MAX),
                    "rate_limit": _f(self.query_one("#setup-rate", Input).value,
                                     RATE_MIN, RATE_MAX),
                },
            },
            "run_params": {
                "tasks": selected,
                "concurrency": _i(self.query_one("#setup-concurrency", Input).value,
                                  CONCURRENCY_MIN, CONCURRENCY_MAX),
                "output_dir": self.query_one("#setup-outdir", Input).value.strip(),
            },
        }

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def _start(self) -> None:
        errors = self._field_errors()
        self._show_field_errors(errors)
        status = self.query_one("#setup-status", Static)
        if errors:
            status.update(
                "[b err]Form belum valid:[/b err] " + ", ".join(sorted(errors.values()))
            )
            return
        collected = self._collect()
        try:
            self._state.apply_config(collected["config_updates"])
        except Exception as e:  # noqa: BLE001 - surface backend validation
            status.update(f"[b err]Gagal simpan config:[/b err] {e}")
            return
        self._state.emit("setup.submitted", collected["run_params"])
        status.update(
            f"[b ok]Config tersimpan ✓[/b ok] — {len(collected['run_params']['tasks'])} "
            f"tasks, concurrency {collected['run_params']['concurrency']}."
        )
        self._state.log("info", "setup submitted")

    def _load_profile(self) -> None:
        self._state.load_config()
        cfg = self._state.config
        provider = str(cfg.get("provider", {}).get("name", "openrouter"))
        experiment = cfg.get("experiment", {}) or {}
        self.query_one("#setup-provider", Select).value = provider
        self.query_one("#setup-model", Input).value = str(
            cfg.get("provider", {}).get("model", _default_model_for(provider))
        )
        self.query_one("#setup-temp", Input).value = str(experiment.get("temperature", 0.2))
        self.query_one("#setup-retries", Input).value = str(experiment.get("max_retries", 3))
        self.query_one("#setup-rate", Input).value = str(experiment.get("rate_limit", 1.5))
        self.query_one("#setup-researcher", Input).value = str(
            cfg.get("researcher", {}).get("name", "")
        )
        self.query_one("#setup-status", Static).update("[dim]Profile dimuat.[/dim]")

    def _set_all_tasks(self, checked: bool) -> None:
        for checkbox in self.query("#setup-tasks Checkbox"):
            checkbox.value = checked  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ #
    # Events
    # ------------------------------------------------------------------ #
    def on_mount(self) -> None:
        super().on_mount()
        self.query_one("#setup-temp", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._show_field_errors(self._field_errors())
        event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "setup-start":
            self._start()
            event.stop()
        elif bid == "setup-load":
            self._load_profile()
            event.stop()
        elif bid == "setup-select-all":
            self._set_all_tasks(True)
            event.stop()
        elif bid == "setup-select-none":
            self._set_all_tasks(False)
            event.stop()
        else:
            super().on_button_pressed(event)
