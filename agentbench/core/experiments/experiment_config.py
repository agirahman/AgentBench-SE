"""Experiment config snapshot writer (experiment.yaml).

Shared by the interactive shell ``run`` command and the legacy CLI. Writing
an ``experiment.yaml`` per experiment keeps runs reproducible (research
feedback item #8).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

DEFAULT_REPOS = {
    "django/django": 10,
    "sympy/sympy": 10,
    "scikit-learn/scikit-learn": 10,
    "matplotlib/matplotlib": 10,
    "psf/requests": 6,
    "mwaskom/seaborn": 4,
}


def save_experiment_config(
    output_dir: str,
    *,
    researcher: dict | None = None,
    provider: dict | None = None,
    experiment: dict | None = None,
    issue_count: int = 0,
    strategy_names: list[str] | None = None,
    experiment_id: str = "",
    agents: list[dict[str, str]] | None = None,
    repos: dict | None = None,
) -> str:
    """Write ``<output_dir>/experiment.yaml`` and return its path.

    Values fall back to the same defaults the legacy CLI used, so a shell
    run and a ``python src/main.py`` run produce equivalent snapshots.
    """
    researcher = researcher or {}
    provider = provider or {}
    experiment = experiment or {}

    model_name = provider.get("model", "unknown")
    provider_name = provider.get("name", "unknown")

    pricing: dict = {}
    try:
        from agentbench.core.evaluation.cost import PricingTable

        pricing = PricingTable.get(model_name) or {}
    except Exception:  # noqa: BLE001 - pricing lookup must never break a run
        pricing = {}

    config = {
        "experiment": {
            "name": "AgentBench-SE Experiment",
            "id": experiment_id,
            "date": datetime.now(timezone.utc).isoformat(),
            "researcher": researcher.get("name", "Agi Rahman Setiadi"),
            "institution": researcher.get("institution", "Universitas Negeri Jakarta"),
        },
        "provider": {
            "name": provider_name,
            "model": model_name,
            "temperature": experiment.get("temperature", 0.2),
            "max_retries": experiment.get("max_retries", 3),
        },
        "dataset": {
            "name": "princeton-nlp/SWE-bench_Lite",
            "repos": repos or DEFAULT_REPOS,
            "n_issues": issue_count,
        },
        "strategies": strategy_names or [],
        "agents": agents or [],
        "pricing": {
            "provider": provider_name,
            "model": model_name,
            "pricing_source": pricing.get("pricing_version", "?"),
            "input_cost_per_1m_tokens": pricing.get("input_per_million", 0),
            "output_cost_per_1m_tokens": pricing.get("output_per_million", 0),
            "currency": "USD",
            "exchange_rate": {
                "from": "USD",
                "to": "IDR",
                "rate": experiment.get("usd_idr_rate", 16500.0),
                "source": "https://www.google.com/finance/beta/quote/USD-IDR",
            },
        },
        "rate_limiting": {
            "enabled": True,
            "mode": "random_uniform",
            "min_seconds": 5,
            "max_seconds": 10,
        },
    }

    out = Path(output_dir) / "experiment.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        yaml.safe_dump(config, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    return str(out)