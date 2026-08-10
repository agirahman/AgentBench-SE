"""AgentBench-SE CLI entry points."""

import sys

# NOTE: no eager import of main here — ``python -m agentbench.cli.main``
# warns "'agentbench.cli.main' found in sys.modules after import of package
# 'agentbench.cli'" when __init__ imports main eagerly (seen in CI/e2e).
__all__ = ["cli"]


def cli() -> None:
    """Proxy to the real CLI (lazy import avoids the sys.modules warning)."""
    from agentbench.cli.main import cli as _cli

    _cli()
