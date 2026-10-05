"""Verify a repo-spec split produces disjoint instance sets.

`select_issues` filters the dataset per repo and takes the FIRST n (`filtered[:n]`),
with `seen` scoped to a single call. So splitting one repo across two runs --
e.g. sympy=8 in half A and sympy=2 in half B -- makes both halves select the
SAME leading instances. The merge step would then report duplicates after both
halves had already burned compute.

This prints the overlap for a proposed split so it is caught before the run.
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dataset_loader import select_issues  # noqa: E402


HALF_A = [("django/django", 10), ("sympy/sympy", 10), ("psf/requests", 6)]
HALF_B = [
    ("scikit-learn/scikit-learn", 10),
    ("matplotlib/matplotlib", 10),
    ("mwaskom/seaborn", 4),
]

# The split that was originally proposed and rejected: it shares scikit-learn and
# sympy across the halves, and select_issues takes the first n per repo, so both
# halves pick the same leading instances. Kept here as the regression case.
BROKEN_A = [("django/django", 10), ("sympy/sympy", 8), ("scikit-learn/scikit-learn", 7)]
BROKEN_B = [
    ("matplotlib/matplotlib", 10),
    ("scikit-learn/scikit-learn", 3),
    ("sympy/sympy", 2),
    ("psf/requests", 6),
    ("mwaskom/seaborn", 4),
]


def check(label: str, a: list[tuple[str, int]], b: list[tuple[str, int]]) -> None:
    ids_a = {i.instance_id for i in select_issues(a)}
    ids_b = {i.instance_id for i in select_issues(b)}
    overlap = sorted(ids_a & ids_b)
    print(f"{label}")
    print(f"  half A: {len(ids_a)}   half B: {len(ids_b)}   union: {len(ids_a | ids_b)}")
    print(f"  overlap: {len(overlap)} {overlap if overlap else ''}")
    repos_a = {i.split('__')[0] for i in ids_a}
    print(f"  repos in A: {sorted(repos_a)}")
    print()


def main() -> None:
    check("FINAL SPLIT (whole repos, no repo in both halves)", HALF_A, HALF_B)
    check("REJECTED SPLIT (repo shared across halves -- duplicates)", BROKEN_A, BROKEN_B)


if __name__ == "__main__":
    main()
