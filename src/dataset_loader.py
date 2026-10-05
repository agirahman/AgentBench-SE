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
    ds = load_dataset("SWE-bench/SWE-bench_Lite", split="test")
    return [dict(row) for row in ds]


def select_issues(
    repo_specs: Iterable[tuple[str, int]] | dict[str, int] | None = None,
    instance_ids: Iterable[str] | None = None,
) -> list[Issue]:
    """Select issues from SWE-bench Lite.

    ``instance_ids`` selects specific instances by id, bypassing ``repo_specs``
    entirely. It exists so a targeted re-run can reproduce one measured failure
    (e.g. django-11001, where review's revision act was starved of turns) without
    running the whole suite. An unknown id raises rather than being skipped: a
    typo would otherwise produce a "successful" run that measured nothing.
    """
    all_data = load_swe_bench_lite()

    if instance_ids is not None:
        wanted = list(dict.fromkeys(instance_ids))  # dedupe, keep order
        by_id = {d["instance_id"]: d for d in all_data}
        missing = [i for i in wanted if i not in by_id]
        if missing:
            raise ValueError(
                f"unknown instance_id(s): {', '.join(missing)}. "
                f"Pass ids exactly as they appear in SWE-bench Lite "
                f"(e.g. django__django-11001, with double underscores)."
            )
        # Emit in dataset order so a subset of a previous run stays comparable.
        order = {d["instance_id"]: i for i, d in enumerate(all_data)}
        wanted.sort(key=lambda i: order[i])
        return [_to_issue(by_id[i]) for i in wanted]

    repo_specs = normalize_repo_specs(repo_specs)
    seen = set()
    issues = []

    for repo, n in repo_specs:
        filtered = [d for d in all_data if d["repo"].startswith(repo) and d["instance_id"] not in seen]
        for d in filtered[:n]:
            seen.add(d["instance_id"])
            issues.append(_to_issue(d))

    return issues


def _to_issue(d: dict) -> Issue:
    return Issue(
        instance_id=d["instance_id"],
        repo=d["repo"],
        base_commit=d["base_commit"],
        problem_statement=d["problem_statement"],
        hints=d.get("hints_text", ""),
        test_patch=d.get("test_patch", "") or "",
    )
