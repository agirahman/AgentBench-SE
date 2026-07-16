import argparse
from datetime import datetime, timezone
from pathlib import Path

from providers.gemini_provider import GeminiProvider
from providers.groq_provider import GroqProvider
from providers.openrouter_provider import OpenRouterProvider
from strategies.direct_strategy import DirectStrategy
from strategies.planning_strategy import PlanningStrategy
from strategies.review_strategy import ReviewStrategy
from experiments.runner import run_experiments
from dataset_loader import select_issues
from utils.logger import logger
from config import Config


def parse_args():
    parser = argparse.ArgumentParser(description="AgentBench-SE Experiment Runner")
    parser.add_argument(
        "--repo",
        default="django",
        help="Filter repo dari SWE-bench (default: django)",
    )
    parser.add_argument(
        "--n-issues",
        type=int,
        default=15,
        help="Jumlah issue yang diproses (default: 15)",
    )
    parser.add_argument(
        "--output",
        default="results",
        help="Direktori output (default: results)",
    )
    parser.add_argument(
        "--provider",
        default="gemini",
        choices=["gemini", "groq", "openrouter"],
        help="Provider AI (default: gemini)",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=1.5,
        help="Delay antar strategy run dalam detik (default: 1.5)",
    )
    return parser.parse_args()


def _save_experiment_config(
    output_dir: str,
    args,
    issue_count: int,
    strategy_names: list[str],
    experiment_id: str = "",
) -> None:
    """Simpan experiment.yaml untuk reproducibility (Kritik #8)."""
    config = {
        "experiment": {
            "name": "AgentBench-SE Experiment",
            "id": experiment_id,
            "date": datetime.now(timezone.utc).isoformat(),
            "researcher": "Agi Rahman Setiadi",
            "institution": "Universitas Negeri Jakarta",
        },
        "provider": {
            "name": args.provider,
            "model": Config.GEMINI_MODEL if args.provider == "gemini" else (Config.OPENROUTER_MODEL if args.provider == "openrouter" else Config.GROQ_MODEL),
            "temperature": Config.TEMPERATURE,
            "max_retries": Config.MAX_RETRIES,
        },
        "dataset": {
            "name": "princeton-nlp/SWE-bench_Lite",
            "filter": args.repo,
            "n_issues": issue_count,
        },
        "strategies": strategy_names,
        "rate_limiting": {
            "enabled": args.rate_limit > 0,
            "interval_seconds": args.rate_limit,
        },
    }
    Path(f"{output_dir}/experiment.yaml").write_text(
        _to_yaml(config), encoding="utf-8"
    )
    logger.info(f"Experiment config saved: {output_dir}/experiment.yaml")


def _to_yaml(data, indent: int = 0) -> str:
    """Serializer YAML sederhana (tanpa dependency)."""
    lines = []
    pad = "  " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            lines.append(_to_yaml(value, indent + 1))
        elif isinstance(value, list):
            lines.append(f"{pad}{key}:")
            for item in value:
                lines.append(f"{pad}  - {item}")
        else:
            lines.append(f"{pad}{key}: {value}")
    return "\n".join(lines) + "\n"


def main():
    args = parse_args()

    if args.provider == "gemini":
        Provider = GeminiProvider
    elif args.provider == "openrouter":
        Provider = OpenRouterProvider
    else:
        Provider = GroqProvider

    provider = Provider()
    if not provider.health_check():
        logger.error("Health check failed — aborting")
        return

    logger.info(f"Loading SWE-bench Lite — repo={args.repo}, n={args.n_issues}")
    issues = select_issues(repo_filter=args.repo, n=args.n_issues)
    logger.info(f"Loaded {len(issues)} issues")

    strategies = {
        "direct": DirectStrategy(provider),
        "planning": PlanningStrategy(provider),
        "review": ReviewStrategy(provider),
    }
    strategy_names = list(strategies.keys())

    Path(args.output).mkdir(parents=True, exist_ok=True)

    df, exp_id = run_experiments(
        issues,
        strategies,
        base_dir=args.output,
        provider_name=args.provider,
        rate_limit_seconds=args.rate_limit,
    )

    # Save experiment.yaml to per-experiment folder
    exp_dir = f"{args.output}/{exp_id}"
    _save_experiment_config(exp_dir, args, len(issues), strategy_names, exp_id)

    print(f"\n=== Experiment {exp_id} completed ===")
    print(f"Output directory: {exp_dir}/")
    summary = df.groupby("strategy")[
        ["execution_time", "inference_count", "total_tokens", "cost_usd"]
    ].mean()
    print("\n=== SUMMARY ===")
    print(summary.to_string())


if __name__ == "__main__":
    main()
