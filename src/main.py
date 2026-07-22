import argparse
from datetime import datetime, timezone
from pathlib import Path

from providers.gemini_provider import GeminiProvider
from providers.groq_provider import GroqProvider
from providers.opencode_provider import OpenCodeProvider
from providers.openrouter_provider import OpenRouterProvider
from strategies.direct_strategy import DirectStrategy
from strategies.planning_strategy import PlanningStrategy
from strategies.review_strategy import ReviewStrategy
from experiments.runner import run_experiments
from dataset_loader import select_issues
from utils.logger import logger
from config import Config
from evaluation.cost import PricingTable


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
        choices=["gemini", "groq", "opencode", "openrouter"],
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
    parser.add_argument(
        "--repo-spec",
        action="append",
        default=[],
        help="Override sampling repo, format: repo=count (mis. django/django=10)",
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
    model_name = (
        Config.GEMINI_MODEL if args.provider == "gemini"
        else Config.GROQ_MODEL if args.provider == "groq"
        else Config.OPENROUTER_MODEL if args.provider == "openrouter"
        else Config.OPENCODE_MODEL
    )
    pricing = PricingTable.get(model_name) or {}
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
            "model": model_name,
            "temperature": Config.TEMPERATURE,
            "max_retries": Config.MAX_RETRIES,
        },
        "dataset": {
            "name": "princeton-nlp/SWE-bench_Lite",
            "repos": {
                "django/django": 10,
                "sympy/sympy": 10,
                "scikit-learn/scikit-learn": 10,
                "matplotlib/matplotlib": 10,
                "psf/requests": 6,
                "mwaskom/seaborn": 4,
            },
            "n_issues": issue_count,
        },
        "strategies": strategy_names,
        "pricing": {
            "provider": args.provider,
            "model": model_name,
            "pricing_source": pricing.get("pricing_version", "?"),
            "input_cost_per_1m_tokens": pricing.get("input_per_million", 0),
            "output_cost_per_1m_tokens": pricing.get("output_per_million", 0),
            "currency": "USD",
            "exchange_rate": {
                "from": "USD",
                "to": "IDR",
                "rate": Config.USD_IDR_RATE,
                "source": "ENV",
            },
        },
        "rate_limiting": {
            "enabled": True,
            "mode": "random_uniform",
            "min_seconds": 5,
            "max_seconds": 10,
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


def _parse_repo_specs(repo_specs: list[str]) -> list[tuple[str, int]]:
    parsed: list[tuple[str, int]] = []
    for spec in repo_specs:
        repo, count_text = spec.split("=", 1)
        parsed.append((repo.strip(), int(count_text.strip())))
    return parsed


def main():
    args = parse_args()

    if args.provider == "gemini":
        Provider = GeminiProvider
    elif args.provider == "groq":
        Provider = GroqProvider
    elif args.provider == "openrouter":
        Provider = OpenRouterProvider
    else:
        Provider = OpenCodeProvider

    provider = Provider()
    if not provider.health_check():
        logger.error("Health check failed — aborting")
        return

    from dataset_loader import DEFAULT_REPO_SPECS
    repo_specs = _parse_repo_specs(args.repo_spec) if args.repo_spec else DEFAULT_REPO_SPECS
    logger.info(f"Loading SWE-bench Lite — multi-repo: {repo_specs}")
    issues = select_issues(repo_specs)
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

    manifest_path = Path(exp_dir) / "manifest.json"
    if manifest_path.exists():
        logger.info(f"Manifest available: {manifest_path}")

    # --- RANGKUMAN HASIL EKSPERIMEN ---
    logger.info(f"Experiment completed: {exp_id}")
    logger.info(f"Output directory: {exp_dir}/")

    # Difficulty breakdown ACTUAL
    difficulty_counts = {"easy": 0, "medium": 0, "hard": 0}
    for issue in issues:
        difficulty_counts[issue.difficulty] += 1
    logger.info(
        f"Actual issues run: {len(issues)} "
        f"(hard={difficulty_counts['hard']}, "
        f"medium={difficulty_counts['medium']}, "
        f"easy={difficulty_counts['easy']})"
    )

    # Total metrics keseluruhan
    total_tokens = df['total_tokens'].sum()
    total_cost = df['cost_usd'].sum()
    avg_time = df['execution_time'].mean()
    logger.info(
        f"Total tokens: {total_tokens:,.0f} | "
        f"Total cost: ${total_cost:.6f} | "
        f"Avg time: {avg_time:.1f}s"
    )

    print(f"\n=== Experiment {exp_id} completed ===")
    print(f"Output directory: {exp_dir}/")

    strategy_summary = df.groupby("strategy")[
        ["execution_time", "inference_count", "total_tokens", "cost_usd"]
    ].mean()
    print("\n=== STRATEGY SUMMARY ===")
    print(strategy_summary.to_string())
    
    if "difficulty" in df.columns:
        diff_summary = df.groupby("difficulty")[
            ["execution_time", "total_tokens", "cost_usd"]
        ].mean()
        print("\n=== DIFFICULTY SUMMARY ===")
        print(diff_summary.to_string())

        # Cost by strategy × difficulty
        cross = df.groupby(["strategy", "difficulty"])["cost_usd"].sum().unstack(fill_value=0)
        print("\n=== COST BY STRATEGY × DIFFICULTY ===")
        print(cross.to_string())


if __name__ == "__main__":
    main()
