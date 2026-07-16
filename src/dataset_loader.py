from datasets import load_dataset
from models.issue import Issue


def load_swe_bench_lite() -> list[dict]:
    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    return [dict(row) for row in ds]


def select_issues(repo_filter: str = "django", n: int = 15) -> list[Issue]:
    all_data = load_swe_bench_lite()
    filtered = [d for d in all_data if d["repo"].startswith(repo_filter)]
    selected = filtered[:n]
    return [
        Issue(
            instance_id=d["instance_id"],
            repo=d["repo"],
            base_commit=d["base_commit"],
            problem_statement=d["problem_statement"],
            hints=d.get("hints_text", ""),
        )
        for d in selected
    ]
