"""``artifacts`` command: browse saved per-issue artifacts."""

from __future__ import annotations

from pathlib import Path

from rich.panel import Panel
from rich.syntax import Syntax

from agentbench.commands.base import BaseCommand


def _find_latest_exp_dir(base: str = "results") -> Path | None:
    root = Path(base)
    if not root.exists():
        return None
    dirs = sorted(
        [p for p in root.iterdir() if p.is_dir() and p.name.startswith("EXP-")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return dirs[0] if dirs else None


class ArtifactsCommand(BaseCommand):
    """Handle ``artifacts <issue_id> <strategy>``."""

    def execute(self, args: str) -> None:
        parts = args.split()
        if len(parts) != 2:
            self.error("Usage: artifacts <issue_id> <strategy>")
            return
        issue_id, strategy = parts

        exp_dir = _find_latest_exp_dir()
        if exp_dir is None:
            self.error("No experiment found in results/. Run 'run' first.")
            return

        base = exp_dir / "artifacts" / issue_id / strategy
        if not base.exists():
            self.error(
                f"No artifacts for {issue_id} / {strategy} "
                f"(looked in {base.parent})."
            )
            return

        files = sorted(p for p in base.iterdir() if p.is_file())
        if not files:
            self.warning(f"Artifact dir empty: {base}")
            return

        for f in files:
            self.console.print(
                f"\n[bold cyan]=== {f.name} ===[/bold cyan]"
            )
            content = f.read_text(encoding="utf-8").rstrip()
            if f.suffix == ".md":
                from rich.markdown import Markdown

                self.console.print(Markdown(content))
            elif f.suffix == ".patch":
                self.console.print(Syntax(content, "diff", word_wrap=True))
            elif f.suffix == ".json":
                try:
                    import json

                    pretty = json.dumps(json.loads(content), indent=2)
                    self.console.print(Syntax(pretty, "json", word_wrap=True))
                except ValueError:
                    self.console.print(content)
            else:
                self.console.print(content)