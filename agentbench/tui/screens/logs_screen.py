"""Logs screen: filterable log viewer (Patch 7 wires it)."""

from agentbench.tui.widgets.shell import ShellScreen


class LogsScreen(ShellScreen):
    nav_key = "logs"
    footer_hint = "Logs — filter level · search · auto-scroll"

    def body(self):
        from textual.widgets import Static

        return [
            Static(
                "[bold accent]Logs[/bold accent]\n\n"
                "[dim] Log viewer lands in Patch 7.[/dim]"
            )
        ]