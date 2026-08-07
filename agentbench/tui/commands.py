"""Help text for the AgentBench TUI command entries."""

from __future__ import annotations

COMMAND_HELP: dict[str, str] = {
    # shell/control (handled natively by the app)
    "help": "Show available commands",
    "info": "Framework info",
    "version": "Show version",
    "setup": "Re-run setup wizard (launches external prompt)",
    "exit": "Exit the TUI",
    # experiments & analysis
    "run": "Start an experiment  (e.g. /run --issues 4 --strategy all)",
    "results": "View results  (summary | compare | errors | patch <id>)",
    "export": "Export results  (--format csv|json|markdown --output PATH)",
    # configuration & data
    "config": "Manage config  (show | set <k> <v> | reset | rate)",
    "provider": "Show/test provider  (--test)",
    "pricing": "Manage pricing  (set/remove/show/refresh)",
    "dataset": "Dataset info  (--refresh)",
    "artifacts": "Browse artifacts  (<issue> <strategy>)",
}