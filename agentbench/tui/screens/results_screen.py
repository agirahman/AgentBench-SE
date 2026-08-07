"""Results screen: sortable results table + drill-down (Patch 6)."""

from agentbench.tui.widgets.shell import ShellScreen


class ResultsScreen(ShellScreen):
    nav_key = "results"
    footer_hint = "Results — sort header · Enter details · Export CSV/JSON"

    def body(self):
        from textual.widgets import Static

        return [
            Static(
                "[bold accent]Results[/bold accent]\n\n"
                "[dim] Results table lands in Patch 6.[/dim]"
            )
        ]