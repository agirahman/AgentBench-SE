from collections.abc import Iterable

from datasets import load_dataset
from models.issue import Issue


DEFAULT_REPO_SPECS: list[tuple[str, int]] = [
    ("django/django", 10),
    ("sympy/sympy", 10),
    ("scikit-learn/scikit-learn", 10),
    ("matplotlib/matplotlib", 10),
    ("psf/requests", 6),
    ("mwaskom/seaborn", 4),
]


def normalize_repo_specs(repo_specs: Iterable[tuple[str, int]] | dict[str, int] | None) -> list[tuple[str, int]]:
    """Normalize repo specs into a list of (repo, count) tuples."""
    if repo_specs is None:
        return list(DEFAULT_REPO_SPECS)
    if isinstance(repo_specs, dict):
        return [(repo, count) for repo, count in repo_specs.items()]
    return list(repo_specs)


def load_swe_bench_lite() -> list[dict]:
    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    return [dict(row) for row in ds]


def select_issues(repo_specs: Iterable[tuple[str, int]] | dict[str, int] | None = None) -> list[Issue]:
    repo_specs = normalize_repo_specs(repo_specs)

    all_data = load_swe_bench_lite()
    seen = set()
    issues = []

    for repo, n in repo_specs:
        filtered = [d for d in all_data if d["repo"].startswith(repo) and d["instance_id"] not in seen]
        for d in filtered[:n]:
            seen.add(d["instance_id"])
            issues.append(
                Issue(
                    instance_id=d["instance_id"],
                    repo=d["repo"],
                    base_commit=d["base_commit"],
                    problem_statement=d["problem_statement"],
                    hints=d.get("hints_text", ""),
                )
            )

    return issues
