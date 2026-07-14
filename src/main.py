import argparse

from providers.gemini_provider import GeminiProvider
from providers.groq_provider import GroqProvider
from strategies.direct_strategy import DirectStrategy
from strategies.planning_strategy import PlanningStrategy
from strategies.review_strategy import ReviewStrategy
from experiments.runner import run_experiments
from dataset_loader import select_issues
from utils.logger import logger


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
        default="groq",
        choices=["gemini", "groq"],
        help="Provider AI (default: gemini)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.provider == "gemini":
        Provider = GeminiProvider
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

    df = run_experiments(issues, strategies, args.output, provider_name=args.provider)

    summary = df.groupby("strategy")[
        ["execution_time", "inference_count", "total_tokens"]
    ].mean()
    print("\n=== SUMMARY ===")
    print(summary.to_string())


if __name__ == "__main__":
    main()
