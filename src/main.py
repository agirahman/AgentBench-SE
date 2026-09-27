import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from providers.gemini_provider import GeminiProvider
from providers.groq_provider import GroqProvider
from providers.opencode_provider import OpenCodeProvider
from providers.openrouter_provider import OpenRouterProvider
from providers.deepseek_provider import DeepSeekProvider
from providers.commandcode_provider import CommandCodeProvider
from agents import tools as T
from agents.registry import build_agent_team
from strategies.direct_strategy import DirectStrategy
from strategies.planning_strategy import PlanningStrategy
from strategies.review_strategy import ReviewStrategy
from experiments.runner import run_experiments
from dataset_loader import select_issues
from utils.logger import logger
from config import Config
from evaluation.cost import PricingTable

# Map CLI --provider flag -> corresponding Config model attribute.
# Any provider not listed here triggers an explicit exit (no silent fallback),
# so experiment.yaml never gets a wrong model name like a previous bug did.
_PROVIDER_MODEL_MAP = {
    "gemini": Config.GEMINI_MODEL,
    "groq": Config.GROQ_MODEL,
    "openrouter": Config.OPENROUTER_MODEL,
    "opencode": Config.OPENCODE_MODEL,
    "deepseek": Config.DEEPSEEK_MODEL,
    "commandcode": Config.COMMANDCODE_MODEL,
}


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
        choices=["gemini", "groq", "opencode", "openrouter", "deepseek", "commandcode"],
        help="Provider AI (default: gemini)",
    )
    parser.add_argument(
        "--strategies",
        nargs="+",
        choices=["direct", "planning", "review"],
        default=["direct", "planning", "review"],
        help="Strategi yang dijalankan (default: semua 3)",
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
    args: argparse.Namespace,
    issue_count: int,
    strategy_names: list[str],
    experiment_id: str,
    agents: list[dict[str, str]],
    repos: dict[str, int] | None = None,
    agent_team: dict | None = None,
) -> None:
    """Simpan experiment.yaml untuk reproducibility (Kritik #8)."""
    model_name = _PROVIDER_MODEL_MAP.get(args.provider)
    if not model_name:
        logger.error(
            f"Model for provider '{args.provider}' not set yet — "
            f"check Config / .env (DEEPSEEK_MODEL, GEMINI_MODEL, etc.)"
        )
        sys.exit(1)
    pricing = PricingTable.get(model_name) or {}
    off_peak = PricingTable.rates_for(model_name, "off_peak")
    peak = PricingTable.rates_for(model_name, "peak")
    config = {
        "experiment_meta": {
            "project": "Skripsi AI Agent SWE-bench Lite",
            "institution": {
                "university": "Universitas Negeri Jakarta",
                "faculty": "Fakultas Teknik",
                "department": "Sistem dan Teknologi Informasi",
            },
            "researcher": {
                "name": "Agi Rahman Setiadi",
                "nim": "1519622032",
                "email_academic": "agi_1519622032@mhs.unj.ac.id",
                "email_personal": "agi.rahman.s@gmail.com",
            },
            "experiment_id": experiment_id,
            "date_created": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        },
        "provider": {
            "name": args.provider,
            "model": model_name,
            "temperature": Config.TEMPERATURE,
            "max_retries": Config.MAX_RETRIES,
            "max_tokens": Config.MAX_TOKENS,
            "api_timeout": Config.API_TIMEOUT,
        },
        "model_thinking": Config.DEEPSEEK_THINKING,
        "model_reasoning_effort": Config.DEEPSEEK_REASONING_EFFORT,
        "review_loop": {
            "max_revision_turns": Config.MAX_REVISION_TURNS,
        },
        "tool_calling": {
            "enabled": Config.TOOLCALL_ENABLED,
            "max_tool_turns": Config.MAX_TOOL_TURNS,
            "repo_dir": Config.TOOLCALL_REPO_DIR,
            "tools": [
                {"agent": name, "tools": list(T.AGENT_TOOLS.get(name, T.TOOL_FUNCTIONS.keys()))}
                for name in (agent_team or {})
            ] if Config.TOOLCALL_ENABLED else [],
        },
        "source_context_enabled": Config.SOURCE_CONTEXT_ENABLED,
        "dataset": {
            "name": "SWE-bench/SWE-bench_Lite",
            "repos": repos or {},
            "n_issues": issue_count,
        },
        "strategies": strategy_names,
        "agents": agents or [],
        "pricing": {
            "provider": args.provider,
            "model": model_name,
            "pricing_source": "https://api-docs.deepseek.com/quick_start/pricing",
            "currency": "USD",
            "off_peak_per_1m_tokens": {
                "input_regular": off_peak["input_per_million"],
                "input_cache_hit": off_peak["cached_input_per_million"],
                "output": off_peak["output_per_million"],
            },
            "peak_per_1m_tokens": {
                "input_regular": peak["input_per_million"],
                "input_cache_hit": peak["cached_input_per_million"],
                "output": peak["output_per_million"],
            },
            "exchange_rate": {
                "from": "USD",
                "to": "IDR",
                "rate": Config.USD_IDR_RATE,
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
    Path(f"{output_dir}/experiment.yaml").write_text(
        _to_yaml(config), encoding="utf-8"
    )
    logger.info(f"Experiment config saved: {output_dir}/experiment.yaml")


def _yaml_scalar(value):
    """Quote scalar strings that YAML would misread (flow indicators, ': ')."""
    if isinstance(value, str) and (
        value.startswith(("{", "[")) or ": " in value or value.strip() != value
    ):
        return json.dumps(value)
    return value


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
                lines.append(f"{pad}  - {_yaml_scalar(item)}")
        else:
            lines.append(f"{pad}{key}: {_yaml_scalar(value)}")
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
    elif args.provider == "deepseek":
        Provider = DeepSeekProvider
    elif args.provider == "commandcode":
        Provider = CommandCodeProvider
    else:
        Provider = OpenCodeProvider

    provider = Provider()
    if hasattr(provider, "health_check"):
        try:
            if not provider.health_check():
                logger.error("Health check failed — aborting")
                return
        except Exception as e:
            logger.warning(f"Health check skipped/unavailable: {e}")

    from dataset_loader import DEFAULT_REPO_SPECS
    repo_specs = _parse_repo_specs(args.repo_spec) if args.repo_spec else DEFAULT_REPO_SPECS
    logger.info(f"Loading SWE-bench Lite — multi-repo: {repo_specs}")
    issues = select_issues(repo_specs)
    logger.info(f"Loaded {len(issues)} issues")

    if args.issues:
        issues = issues[:args.issues]
        logger.info(f"Limit: testing with {len(issues)} issues")

    agent_team = build_agent_team(provider)
    agents = [
        {"name": name, "prompt_file": agent.prompt_file}
        for name, agent in agent_team.items()
    ]

    all_strategies = {
        "direct": DirectStrategy(provider),
        "planning": PlanningStrategy(provider),
        "review": ReviewStrategy(provider),
    }
    # Only instantiate/run the strategies selected via --strategies.
    strategies = {name: all_strategies[name] for name in args.strategies}
    strategy_names = list(strategies.keys())
    logger.info(f"Strategies selected: {strategy_names}")

    Path(args.output).mkdir(parents=True, exist_ok=True)

    df, exp_id = run_experiments(
        issues,
        strategies,
        base_dir=args.output,
        provider_name=args.provider,
        rate_limit_seconds=args.rate_limit,
        resume=args.resume,
        agents=agents,
        model=provider.model,
    )

    # Save experiment.yaml to per-experiment folder
    exp_dir = f"{args.output}/{exp_id}"
    repos_actual = dict(Counter(issue.repo for issue in issues))
    _save_experiment_config(exp_dir, args, len(issues), strategy_names, exp_id, agents, repos_actual, agent_team)

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
    # Cost columns were renamed across schema generations
    # (``cost_usd`` → ``cost_usd_offpeak`` → ``cost_usd_actual``); accept all.
    cost_col = next(
        (
            c
            for c in ("cost_usd_actual", "actual_cost_usd", "cost_usd_offpeak", "cost_usd")
            if c in df.columns
        ),
        None,
    )
    total_tokens = df['total_tokens'].sum()
    total_cost = df[cost_col].sum() if cost_col else 0.0
    avg_time = df['execution_time'].mean()
    logger.info(
        f"Total tokens: {total_tokens:,.0f} | "
        f"Total cost: ${total_cost:.6f} | "
        f"Avg time: {avg_time:.1f}s"
    )

    print(f"\n=== Experiment {exp_id} completed ===")
    print(f"Output directory: {exp_dir}/")

    summary_cols = ["execution_time", "inference_count", "total_tokens"]
    if cost_col:
        summary_cols.append(cost_col)
    strategy_summary = df.groupby("strategy")[summary_cols].mean()
    print("\n=== STRATEGY SUMMARY ===")
    print(strategy_summary.to_string())
    
    if "difficulty" in df.columns:
        diff_summary = df.groupby("difficulty")[summary_cols].mean()
        print("\n=== DIFFICULTY SUMMARY ===")
        print(diff_summary.to_string())

        # Cost by strategy × difficulty
        if cost_col:
            cross = df.groupby(["strategy", "difficulty"])[cost_col].sum().unstack(fill_value=0)
            print("\n=== COST BY STRATEGY × DIFFICULTY ===")
            print(cross.to_string())


if __name__ == "__main__":
    main()
