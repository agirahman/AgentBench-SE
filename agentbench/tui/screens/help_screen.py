"""Help screen: keyboard cheatsheet (Patch 8 wires it)."""

from agentbench.tui.widgets.shell import ShellScreen


class HelpScreen(ShellScreen):
    nav_key = "help"
    footer_hint = "Help — shortcut cheatsheet"

    def body(self):
        from textual.widgets import Static

        return [
            Static(
                "[bold accent]Help[/bold accent]\n\n"
                "[dim] Cheatsheet lands in Patch 8.[/dim]"
            )
        ]