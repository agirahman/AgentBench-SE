import argparse
from datetime import datetime, timezone
from pathlib import Path

from providers.gemini_provider import GeminiProvider
from providers.groq_provider import GroqProvider
from providers.opencode_provider import OpenCodeProvider
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
        "--output",
        default="results",
        help="Direktori output (default: results)",
    )
    parser.add_argument(
        "--provider",
        default="gemini",
        choices=["gemini", "groq", "opencode"],
        help="Provider AI (default: gemini)",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=1.5,
        help="Delay antar strategy run dalam detik (default: 1.5)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Lanjutkan eksperimen sebelumnya — skip issue yang sudah selesai",
    )
    parser.add_argument(
        "--issues",
        type=int,
        default=None,
        help="Batas jumlah issue untuk testing (default: semua 50)",
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
            "model": (
            Config.GEMINI_MODEL if args.provider == "gemini"
            else Config.GROQ_MODEL if args.provider == "groq"
            else Config.OPENCODE_MODEL
        ),
            "temperature": Config.TEMPERATURE,
            "max_retries": Config.MAX_RETRIES,
        },
        "dataset": {
            "name": "princeton-nlp/SWE-bench_Lite",
            "repos": {
                "psf/requests": 20,
                "mwaskom/seaborn": 15,
                "django/django": 15,
            },
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
    elif args.provider == "groq":
        Provider = GroqProvider
    else:
        Provider = OpenCodeProvider

    provider = Provider()
    if not provider.health_check():
        logger.error("Health check failed — aborting")
        return

    from dataset_loader import DEFAULT_REPO_SPECS
    logger.info(f"Loading SWE-bench Lite — multi-repo: {DEFAULT_REPO_SPECS}")
    issues = select_issues()
    logger.info(f"Loaded {len(issues)} issues")
    
    if args.issues:
        issues = issues[:args.issues]
        logger.info(f"Limit: testing with {len(issues)} issues")

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
        resume=args.resume,
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
