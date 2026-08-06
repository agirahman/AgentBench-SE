"""``dataset`` command: show SWE-bench Lite info (cached) or refresh."""

from __future__ import annotations

from pathlib import Path

from rich.table import Table

from agentbench.commands.base import BaseCommand


class DatasetCommand(BaseCommand):
    """Handle ``dataset [--refresh]``."""

    def execute(self, args: str) -> None:
        refresh = "--refresh" in args

        from agentbench.config_manager import ConfigManager

        cm = ConfigManager()
        cache = cm.dataset_cache_dir

        if refresh:
            self.info("Re-downloading dataset from HuggingFace...")
            issues = self._download()
            self._write_cache(cache, issues)
            self.success(f"Dataset refreshed ({len(issues)} issues).")
            self._render(issues)
            return

        issues = self._read_cache(cache)
        if issues is None:
            self.warning(
                "Dataset not cached. Run 'dataset --refresh' to download "
                "SWE-bench Lite first."
            )
            return
        self.info(f"Cached dataset (SWE-bench Lite, {len(issues)} issues):")
        self._render(issues)

    # ------------------------------------------------------------------ #
    def _download(self) -> list:
        from agentbench.core.dataset_loader import select_issues

        return select_issues()  # uses DEFAULT_REPO_SPECS

    def _read_cache(self, cache: Path) -> list | None:
        import json

        path = cache / "dataset.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("issues", [])
        return None

    def _write_cache(self, cache: Path, issues: list) -> None:
        import json

        cache.mkdir(parents=True, exist_ok=True)
        (cache / "dataset.json").write_text(
            json.dumps(
                [{"instance_id": i.instance_id, "repo": i.repo,
                  "difficulty": getattr(i, "difficulty", "unknown")}
                 for i in issues],
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )

    def _render(self, issues: list) -> None:
        from collections import Counter

        repos = Counter(i.get("repo", "unknown") if isinstance(i, dict)
                        else getattr(i, "repo", "unknown") for i in issues)
        diffs = Counter(i.get("difficulty", "unknown") if isinstance(i, dict)
                        else getattr(i, "difficulty", "unknown") for i in issues)

        table = Table(title="SWE-bench Lite Dataset", box=None)
        table.add_column("Repo", style="cyan")
        table.add_column("Issues", justify="right")
        for repo, cnt in repos.most_common():
            table.add_row(repo, str(cnt))

        self.console.print(table)

        diff_t = Table(title="Difficulty Distribution", box=None)
        diff_t.add_column("Difficulty", style="cyan")
        diff_t.add_column("Count", justify="right")
        for d, cnt in diffs.most_common():
            diff_t.add_row(str(d), str(cnt))
        self.console.print(diff_t)