"""Setup screen: experiment configuration form (Patch 3 wires the form)."""

from agentbench.tui.widgets.shell import ShellScreen


class SetupScreen(ShellScreen):
    nav_key = "setup"
    footer_hint = "Setup — configure provider/model/tasks, then Start"

    def body(self):
        from textual.widgets import Static

        return [
            Static(
                "[b accent]Experiment Configuration[/b accent]\n\n"
                "[dim]Setup form lands in Patch 3.[/dim]"
            )
        ]