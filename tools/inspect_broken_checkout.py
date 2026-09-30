"""A checkout whose HEAD is the null SHA -- is it real, and does it matter?

``tools/clean_repos.py`` reported one directory whose HEAD is
``0000000000000000000000000000000000000000``. That is the null object ID, which git
reports for a repository with no valid HEAD -- typically an interrupted clone or a
half-created checkout.

It was NOT in the earlier dirty list, because a broken-HEAD repo has no working
tree state to report as dirty: ``git status`` fails rather than listing files. So it
was invisible to the dirty check and only surfaced here.

The question that matters: is it a checkout the sweep would use? A path named after
the null SHA is not any instance's base_commit, so the runner should never resolve
to it -- but that needs confirming, not assuming.

Nothing is modified by this script.

Usage:
    python tools/inspect_broken_checkout.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_BASE = ROOT / "datasets" / "repos"


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "") + (p.stderr or "")


def main() -> None:
    null_dirs = [
        g.parent for g in sorted(REPO_BASE.rglob(".git"))
        if g.parent.name == "0" * 40
    ]

    print("=" * 78)
    print(f"  directories named after the null SHA: {len(null_dirs)}")
    print("=" * 78)

    for d in null_dirs:
        print(f"\n  {d.relative_to(ROOT)}")
        size_mb = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 1e6
        n_files = sum(1 for f in d.rglob("*") if f.is_file())
        print(f"    size      : {size_mb:.1f} MB in {n_files} files")
        print(f"    git HEAD  : {git(d, 'rev-parse', '--verify', 'HEAD').strip()[:100]}")
        print(f"    branches  : {git(d, 'branch', '-a').strip()[:200]}")
        print(f"    remotes   : {git(d, 'remote', '-v').strip()[:200]}")
        packed = (d / ".git" / "packed-refs").exists()
        objects = list((d / ".git" / "objects").glob("??")) if (d / ".git" / "objects").is_dir() else []
        print(f"    packed-refs: {packed}   loose object dirs: {len(objects)}")

    print()
    print("=" * 78)
    print("  WOULD THE SWEEP USE IT?")
    print("=" * 78)
    from datasets import load_dataset

    ds = load_dataset("SWE-bench/SWE-bench_Lite", split="test")
    null_shas = {d.name for d in null_dirs}
    collisions = [
        r["instance_id"] for r in ds
        if r["base_commit"] in null_shas
    ]
    print(f"\n  instances whose base_commit is the null SHA: {len(collisions)}")
    if collisions:
        print(f"    {collisions}")
        print("    -> these WOULD resolve to the broken checkout. Fix required.")
    else:
        print("    none -- no instance resolves to a directory named 0{40}")
        print("    -> the directory is inert: the sweep never selects it.")
        print()
        print("    It is still worth removing to stop it appearing in every scan,")
        print("    but it is NOT a blocker and NOT data loss: a null-HEAD repo")
        print("    contains no checkout of any instance.")


if __name__ == "__main__":
    main()
