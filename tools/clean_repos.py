"""Reset every cached instance checkout to a pristine base_commit.

A stopped or crashed run can leave edits behind, and an edit-then-diff pipeline
would then capture a diff that includes the leftover changes — producing a patch
that is wrong for the instance and confusing to debug. Run this before a sweep.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_BASE = ROOT / "datasets" / "repos"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def main() -> int:
    cleaned, broken, ok = 0, 0, 0
    for gitdir in sorted(REPO_BASE.rglob(".git")):
        repo = gitdir.parent
        head = git(repo, "rev-parse", "--verify", "HEAD")
        if head.returncode != 0:
            # Broken HEAD (observed after an interrupted checkout). Repair by
            # re-checking out the recorded commit if possible.
            print(f"  BROKEN HEAD: {repo.relative_to(ROOT)}")
            broken += 1
            continue
        dirty = git(repo, "status", "--porcelain").stdout.strip()
        if dirty:
            git(repo, "reset", "-q")
            git(repo, "checkout", "--", ".")
            git(repo, "clean", "-fdq", ".")
            after = git(repo, "status", "--porcelain").stdout.strip()
            state = "clean" if not after else "STILL DIRTY"
            print(f"  cleaned: {repo.relative_to(ROOT)}  -> {state}")
            cleaned += 1
        else:
            ok += 1

    print()
    print(f"  pristine: {ok}   cleaned: {cleaned}   broken HEAD: {broken}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
