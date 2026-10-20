import argparse
import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from providers.gemini_provider import GeminiProvider
from providers.groq_provider import GroqProvider
from providers.opencode_provider import OpenCodeProvider
from providers.meta_provider import MetaProvider
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
    "meta": Config.META_MODEL,
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
        choices=["gemini", "groq", "opencode", "meta", "openrouter", "deepseek", "commandcode"],
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
        help=(
            "Lanjutkan eksperimen yang sudah ada — skip issue yang sudah selesai. "
            "Butuh --exp-id: tanpa itu tidak ada eksperimen yang bisa dilanjutkan."
        ),
    )
    parser.add_argument(
        "--exp-id",
        default=None,
        help=(
            "Tulis ke direktori eksperimen ini (mis. EXP-20260929-022) alih-alih "
            "membuat ID baru. Dipakai bersama --resume untuk melanjutkan run yang "
            "terputus. Tanpa --resume, ID ini dijalankan ulang dari awal."
        ),
    )
    parser.add_argument(
        "--issues",
        type=int,
        default=None,
        help="Batas jumlah issue untuk testing (default: semua 50)",
    )
    parser.add_argument(
        "--allow-config-change",
        action="store_true",
        help=(
            "Izinkan --resume pada eksperimen yang dicatat dengan provider/model/"
            "thinking berbeda. Tanpa flag ini resume semacam itu ditolak (exit 2) "
            "karena kunci resume memuat model+thinking: run lama akan dijalankan "
            "ULANG dan dua konfigurasi tercampur dalam satu folder."
        ),
    )
    parser.add_argument(
        "--repo-spec",
        action="append",
        default=[],
        help="Override sampling repo, format: repo=count (mis. django/django=10)",
    )
    parser.add_argument(
        "--instance-ids",
        nargs="+",
        default=None,
        help=(
            "Jalankan instance tertentu saja, mis. django__django-11001. "
            "Mengabaikan --repo-spec. Dipakai untuk re-run bertarget satu kegagalan "
            "yang sudah terukur, bukan untuk run besar."
        ),
    )
    return parser.parse_args()


def _previous_dataset_ids(output_dir: str) -> list[str]:
    """Instance ids an existing experiment already recorded.

    Two sources, because a resume can find either: the ids written in
    ``experiment.yaml`` by an earlier invocation, and the ids present in the
    ``predictions/*.jsonl`` savepoints (which exist even when the YAML was never
    written -- a crash before the first export).

    Best-effort by design: an unreadable YAML or a corrupt savepoint must not stop
    a sweep. An unreadable YAML is copied to ``experiment.yaml.bak`` first so the
    old record is not lost, and that is the only case where a warning is raised.
    """
    ids: list[str] = []
    seen: set[str] = set()

    def add(iid) -> None:
        if iid and iid not in seen:
            seen.add(iid)
            ids.append(iid)

    yaml_path = Path(output_dir) / "experiment.yaml"
    if yaml_path.is_file():
        try:
            import yaml

            cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            for iid in ((cfg.get("dataset") or {}).get("instance_ids") or []):
                add(iid)
        except Exception as exc:  # noqa: BLE001 - metadata must not kill a run
            logger.warning(
                f"Could not read {yaml_path} ({type(exc).__name__}: {exc}); "
                f"keeping a .bak copy and continuing."
            )
            try:
                shutil.copy2(yaml_path, yaml_path.with_name(yaml_path.name + ".bak"))
            except OSError as copy_exc:
                logger.warning(f"Could not write the experiment.yaml backup: {copy_exc}")

    pred_dir = Path(output_dir) / "predictions"
    if pred_dir.is_dir():
        for jsonl in sorted(pred_dir.glob("*.jsonl")):
            if jsonl.name == "predictions.jsonl":
                continue
            try:
                with open(jsonl, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(entry, dict):
                            add(entry.get("instance_id"))
            except (OSError, UnicodeDecodeError):
                continue
    return ids


def _merged_dataset_ids(output_dir: str, batch_ids: list[str] | None) -> list[str] | None:
    """Old experiment ids + this batch, deduped, experiment order first.

    This is what keeps ``experiment.yaml`` from shrinking on a resume. It used to
    be written from the CURRENT batch only, so a 10-issue experiment resumed with
    a 1-issue batch recorded ``n_issues: 1`` while its CSV held 10 issues
    (measured on EXP-20261012-005).
    """
    if batch_ids is None:
        return None
    merged = _previous_dataset_ids(output_dir)
    seen = set(merged)
    for iid in batch_ids:
        if iid not in seen:
            seen.add(iid)
            merged.append(iid)
    return merged


def _repo_for_instance(instance_id: str) -> str:
    """``django__django-11099`` -> ``django/django``.

    Derived rather than looked up so the YAML's ``repos`` can be computed from the
    MERGED id list without touching the dataset. Verified exact on SWE-bench Lite:
    the derivation reproduces every row's ``repo`` (0 mismatches), so a resume that
    records 10 merged ids does not report ``repos: {django/django: 1}``.

    An id with no ``-`` at all (or no stem before it) is returned unchanged,
    because there is nothing to split. Anything else splits at the LAST ``-``
    whatever follows it, so ``django__django-11099`` -> ``django/django`` but a
    hypothetical ``foo-bar`` -> ``foo``: the split is positional, not
    digit-aware. That is deliberate for SWE-bench ids, which are always
    ``<owner>__<name>-<number>``.
    """
    stem, sep, _ = instance_id.rpartition("-")
    if not sep or not stem:
        return instance_id
    return stem.replace("__", "/")


def _repos_from_instance_ids(instance_ids: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for iid in instance_ids:
        repo = _repo_for_instance(str(iid))
        counts[repo] = counts.get(repo, 0) + 1
    return counts


def _run_identity(provider: str) -> dict:
    """The identity a resume must not silently change.

    One block, written by ``_save_experiment_config`` as ``run_identity`` and
    compared against the live invocation. The resume key in the runner is
    ``instance_id|model|thinking``: resuming with another model or thinking flag
    makes every finished run look outstanding, so the batch is PAID FOR AGAIN and
    two configurations end up mixed in one folder's numbers.
    """
    return {
        "provider": provider,
        "model": _PROVIDER_MODEL_MAP.get(provider),
        "thinking": bool(Config.DEEPSEEK_THINKING),
    }


def _check_resume_identity(
    output_dir: str,
    experiment_id: str,
    identity: dict,
    allow_change: bool = False,
    has_savepoints: bool | None = None,
) -> str | None:
    """Return a refusal message when a resume changes the recorded run identity.

    ``None`` means "go ahead". The rules, in order:

    * no ``experiment.yaml`` -> nothing was recorded, so nothing can conflict.
    * block present and equal -> resume normally.
    * block present and different -> refuse, naming each field that changed.
      ``--allow-config-change`` skips this refusal.
    * block ABSENT (YAML from before the block existed) -> "unknown". An
      experiment with no savepoint yet is still resumable: there is no finished
      work to mix with. Once savepoints exist, resuming blind is exactly the
      case this guard exists to stop, so it needs the explicit opt-out.

    ``has_savepoints`` is injectable for tests; by default the predictions
    folder is inspected, which is the same evidence the runner's resume scan
    uses.
    """
    cfg_path = Path(output_dir) / experiment_id / "experiment.yaml"
    if not cfg_path.is_file():
        return None
    try:
        import yaml

        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - unreadable != mismatched
        logger.warning(
            f"Could not read {cfg_path} for the resume identity check "
            f"({type(exc).__name__}: {exc}); continuing."
        )
        return None

    if not isinstance(cfg, dict):
        # Root is a list/scalar (a legacy or damaged file). That says nothing
        # about the run's identity, so it must not crash the check: the
        # "unreadable -> continue" rule applies to a parseable-but-wrong SHAPE
        # as much as to a parse error.
        logger.warning(
            f"{cfg_path} does not contain a mapping at the top level; "
            f"skipping the resume identity check."
        )
        return None

    recorded = cfg.get("run_identity")
    if not isinstance(recorded, dict):
        if has_savepoints is None:
            has_savepoints = _has_savepoints(Path(output_dir) / experiment_id)
        if not has_savepoints or allow_change:
            return None
        return (
            f"{cfg_path} has no run_identity block, so the provider/model/thinking "
            f"that produced its {experiment_id} savepoints is unknown. Resuming "
            f"would mix two configurations in one folder. Re-run with "
            f"--allow-config-change if that is intended."
        )

    changed = [
        f"{field}: recorded {recorded.get(field)!r}, now {identity.get(field)!r}"
        for field in ("provider", "model", "thinking")
        if recorded.get(field) != identity.get(field)
    ]
    if not changed:
        return None
    if allow_change:
        logger.warning(
            "Resuming despite a changed run identity (--allow-config-change): "
            + "; ".join(changed)
        )
        return None
    return (
        f"Resuming {experiment_id} would change the run identity: "
        + "; ".join(changed)
        + ". Every finished run would be re-run and two configurations mixed in "
        "one experiment. Use --allow-config-change if that is intended, or start "
        "a new experiment."
    )


def _has_savepoints(exp_dir: Path) -> bool:
    """True when the folder already holds evidence that a run finished.

    Two sources, because legacy folders may have only one of them. The CSV is
    checked as well as the jsonl savepoints: a folder whose predictions were
    pruned but whose ``generation_result.csv`` still holds rows HAS run, and
    calling it "never ran" would let a resume mix configurations silently --
    the exact hole this classification exists to close.
    """
    pred_dir = exp_dir / "predictions"
    if pred_dir.is_dir():
        for jsonl in pred_dir.glob("*.jsonl"):
            try:
                if jsonl.stat().st_size > 0:
                    return True
            except OSError:
                continue
    csv_path = exp_dir / "generation_result.csv"
    try:
        # A header-only CSV is not evidence of a run; anything after it is.
        # The REST of the file is read, not just the second line: the header may
        # be followed by a blank line, and reading one line would then call a
        # folder full of runs "never ran".
        with csv_path.open("r", encoding="utf-8", errors="replace") as f:
            if not f.readline().strip():
                return False
            return bool(f.read().strip())
    except OSError:
        return False


def _save_experiment_config(
    output_dir: str,
    args: argparse.Namespace,
    issue_count: int,
    strategy_names: list[str],
    experiment_id: str,
    agents: list[dict[str, str]],
    repos: dict[str, int] | None = None,
    agent_team: dict | None = None,
    instance_ids: list[str] | None = None,
) -> None:
    """Simpan experiment.yaml untuk reproducibility (Kritik #8).

    ``instance_ids`` is this batch's ids. When given, ``dataset.n_issues`` and
    ``dataset.instance_ids`` describe the whole experiment -- the union of what an
    earlier invocation recorded and this batch -- so a resumed sweep never shrinks
    its own record. The old file is copied to ``experiment.yaml.bak`` before it is
    replaced by a DIFFERENT set of ids.
    """
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
    # getattr: callers that build args by hand (tests, tooling) predate this flag.
    targeted_ids = getattr(args, "instance_ids", None)
    if instance_ids is not None:
        # Resume path: record the WHOLE experiment, not just this batch.
        targeted_ids = _merged_dataset_ids(output_dir, instance_ids)
        issue_count = len(targeted_ids)
        # Repos must describe the SAME id set as n_issues: one batch's repo counts
        # next to the merged total reads as "10 issues, all in django".
        repos = _repos_from_instance_ids(targeted_ids)
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
        # The identity a resume must not silently change; compared by
        # _check_resume_identity before the first run of a resumed invocation.
        "run_identity": _run_identity(args.provider),
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
            "total_tool_turns": Config.TOTAL_TOOL_TURNS,
            # Separate from total_tool_turns: only review's revision acts draw
            # it, so review's total is total + this when non-zero. Recorded here
            # because a run that used it is not directly comparable to one that
            # did not (EXP-20260928-003 ran with 0).
            "revision_tool_turns": Config.REVISION_TOOL_TURNS,
            # How the pool is divided. These three decide the result as much as
            # total_tool_turns does, so a run that omits them cannot be compared
            # to a later one: per_act caps the first act (13 of 40 for review),
            # per_task lets it draw the remainder minus the floor. EXP-003 ran
            # per_act with no floor; the budget curve varies both.
            "budget_mode": Config.BUDGET_MODE,
            "budget_floor_per_act": Config.BUDGET_FLOOR_PER_ACT,
            "cost_limit_usd": Config.COST_LIMIT_USD,
            # Non-empty means every cost column is MODELLED, not billed: the
            # run used a free model and the tokens were priced with this card.
            "pricing_model_override": Config.PRICING_MODEL_OVERRIDE or None,
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
            # A targeted re-run selects by id instead of by repo count; recording
            # the ids is what makes it reproducible (the repos map is then only
            # a summary of what those ids happened to be).
            "instance_ids": list(targeted_ids) if targeted_ids else None,
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
            # No exchange_rate block: the pipeline reports USD only, and the rate is
            # chosen at reporting time (tools/convert_to_idr.py --rate ...). Carrying
            # a rate here would make batches run at different times incomparable.
        },
        "rate_limiting": {
            "enabled": True,
            "mode": "random_uniform",
            "min_seconds": 5,
            "max_seconds": 10,
        },
    }
    yaml_path = Path(f"{output_dir}/experiment.yaml")
    new_text = _to_yaml(config)
    if instance_ids is not None:
        _backup_yaml_before_rewrite(yaml_path, targeted_ids or [], new_text)
    yaml_path.write_text(new_text, encoding="utf-8")
    logger.info(f"Experiment config saved: {output_dir}/experiment.yaml")


def _backup_yaml_before_rewrite(
    yaml_path: Path, new_ids: list[str], new_text: str | None = None,
) -> None:
    """Keep the previous experiment.yaml as .bak when it will actually change.

    The `.bak` name is FIXED, so it holds exactly ONE previous state: the file
    as it was before the most recent rewrite. That makes "what did the previous
    batch record?" answerable; it does NOT keep a history of every batch, and a
    later rewrite replaces it. (Do not read `.bak` as "batch 1".)

    The trigger is a DIFFERENT ID SET or a different FILE CONTENT: a resume with
    another provider/pricing leaves the ids alone but still overwrites the record
    of how the previous batch was produced. Identical content writes no `.bak`,
    so the second ``_save_experiment_config`` call in one invocation does not
    replace the previous copy with an identical merged state.
    """
    if not yaml_path.is_file():
        return
    try:
        import yaml

        old_text = yaml_path.read_text(encoding="utf-8")
        old = yaml.safe_load(old_text) or {}
        old_ids = list((old.get("dataset") or {}).get("instance_ids") or [])
    except Exception:  # noqa: BLE001 - unreadable file: still keep a copy
        old_ids = None
        old_text = None
    if old_ids is not None and set(old_ids) == set(new_ids):
        # Same id set: only a content change (provider/model/pricing/...) still
        # deserves a copy. ``new_text`` is optional so existing callers that pass
        # only ids keep working; without it, identical ids mean no rewrite.
        if new_text is None or old_text == new_text:
            return
    try:
        shutil.copy2(yaml_path, yaml_path.with_name(yaml_path.name + ".bak"))
    except OSError as exc:
        logger.warning(f"Could not write the experiment.yaml backup: {exc}")


def _yaml_scalar(value):
    """Render a scalar so a YAML loader reads it back as the same value.

    ``None`` must become ``null``: returning the object leaves the f-string to
    render it as the string ``"None"``, which yaml.safe_load reads back as the
    STRING 'None' rather than as null. That silently turns "no targeted ids"
    into "an id named None" for anyone reading experiment.yaml.
    """
    if value is None:
        return "null"
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
            if not value:
                # An empty list must not render as a bare "key:" — a YAML
                # loader reads that back as null, so a consumer doing
                # len(cfg["agents"]) would crash on a field that is meant to
                # be an empty list.
                lines.append(f"{pad}{key}: []")
                continue
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
    elif args.provider == "meta":
        Provider = MetaProvider
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
    if args.instance_ids:
        # A targeted re-run: --instance-ids wins over --repo-spec, so the same
        # command cannot silently measure a different set than the one asked for.
        logger.info(f"Targeted run — instances: {args.instance_ids}")
        issues = select_issues(instance_ids=args.instance_ids)
    else:
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

    # Before any paid run: a resume must not silently change the recorded run
    # identity. Exit 2 so a wrapper cannot mistake this refusal for a run result.
    if args.resume and getattr(args, "exp_id", None):
        refusal = _check_resume_identity(
            args.output, args.exp_id, _run_identity(args.provider),
            allow_change=getattr(args, "allow_config_change", False),
        )
        if refusal:
            logger.error(refusal)
            return 2

    # On a resume the batch is a SUBSET of the experiment. Passing the batch ids
    # makes _save_experiment_config record the union (old ids + this batch), so
    # experiment.yaml never shrinks when a smaller batch continues a folder.
    batch_ids = [i.instance_id for i in issues] if args.resume else None

    def _write_early_config(exp_dir: str, exp_id: str) -> None:
        """Record the configuration as soon as the experiment directory exists.

        Previously this only happened after every run finished, so a crash, a
        kill, or a full disk left a directory of patches with no record of the
        settings that produced them -- and the longer the run, the more that
        matters. A 6-hour sweep is exactly that case.
        """
        repos_actual = dict(Counter(issue.repo for issue in issues))
        _save_experiment_config(
            exp_dir, args, len(issues), strategy_names, exp_id, agents, repos_actual,
            agent_team, instance_ids=batch_ids,
        )

    df, exp_id = run_experiments(
        issues,
        strategies,
        base_dir=args.output,
        provider_name=args.provider,
        rate_limit_seconds=args.rate_limit,
        resume=args.resume,
        agents=agents,
        model=provider.model,
        on_experiment_start=_write_early_config,
        experiment_id=getattr(args, "exp_id", None),
    )

    # Save experiment.yaml to per-experiment folder
    exp_dir = f"{args.output}/{exp_id}"
    repos_actual = dict(Counter(issue.repo for issue in issues))
    _save_experiment_config(
        exp_dir, args, len(issues), strategy_names, exp_id, agents, repos_actual,
        agent_team, instance_ids=batch_ids,
    )

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

    # A rate-limit early stop must reach the shell as a non-zero rc. The runner
    # already writes MERGE_LOG/INCOMPLETE.json and marks the frame; without this
    # the process still exited 0, so a sweep that stopped on an exhausted usage
    # window looked like a clean run -- and sweep_started.json (which records the
    # child's rc) recorded rc=0 with it. Return 1 so the caller's rc matches what
    # the run actually did and cannot be mistaken for success.
    if df.attrs.get("rate_limit_stopped"):
        logger.warning("Exiting 1: the run stopped early on a provider rate limit.")
        return 1


if __name__ == "__main__":
    # Propagate main()'s rc: a rate-limit early stop returns 1, and the wrapper
    # tools/run_with_env.py records that child rc into sweep_started.json.
    sys.exit(main())
