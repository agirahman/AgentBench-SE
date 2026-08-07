"""Run screen: live experiment dashboard (Patch 5 wires the runner)."""

from agentbench.tui.widgets.shell import ShellScreen


class RunScreen(ShellScreen):
    nav_key = "run"
    footer_hint = "Run — Space pause · S stop · L logs"

    def body(self):
        from textual.widgets import Static

        return [
            Static(
                "[bold accent]Run[/bold accent]\n\n"
                "[dim] Live dashboard lands in Patch 5.[/dim]"
            )
        ]