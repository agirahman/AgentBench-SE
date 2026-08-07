"""Config screen: provider/model/pricing/budget (Patch 4 wires it)."""

from agentbench.tui.widgets.shell import ShellScreen


class ConfigScreen(ShellScreen):
    nav_key = "config"
    footer_hint = "Config — provider/model switch · live pricing · budget"

    def body(self):
        from textual.widgets import Static

        return [
            Static(
                "[bold accent]Config[/bold accent]\n\n"
                "[dim] Provider/pricing/budget lands in Patch 4.[/dim]"
            )
        ]