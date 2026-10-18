"""Reset every cached instance checkout to a pristine base_commit.

A stopped or crashed run can leave edits behind, and an edit-then-diff pipeline
would then capture a diff that includes the leftover changes — producing a patch
that is wrong for the instance and confusing to debug. Run this before a sweep.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_BASE = ROOT / "datasets" / "repos"

#: An instance checkout directory is named after its base_commit (40 hex chars).
#: This is the SAME shape tools/preflight_repos.py resolves via the dataset rows.
_INSTANCE_DIR_RE = re.compile(r"^[0-9a-f]{40}$")


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def is_instance_checkout(git_dir: Path) -> bool:
    """True when ``git_dir`` is ``<repo>/<name>/<base_commit>/.git``.

    REQUIRED, because ``datasets/repos/.git`` exists as an EMPTY directory (it is
    not a repository). ``rglob('.git')`` returns it, and ``git -C datasets/repos``
    then walks UP the tree and resolves to the PROJECT's own ``.git`` — so an
    unvalidated loop would run ``git reset`` / ``checkout`` / ``clean`` against the
    research repository itself. Measured: that is why this script reported 54
    "checkouts" while only 53 exist.

    The dangerous part is ``git reset -q``: it takes NO pathspec, so it resets the
    entire index of whatever repository git resolves. The other two commands were
    contained only by the ``.`` pathspec and by git's refusal to descend into
    nested repositories.
    """
    if not _INSTANCE_DIR_RE.match(git_dir.parent.name):
        return False
    # A real checkout's .git holds HEAD; the empty decoy does not.
    return (git_dir / "HEAD").exists()


def clean_one(repo: Path) -> str:
    """Reset one checkout. Returns 'pristine' | 'cleaned' | 'dirty' | 'broken'.

    Extracted from ``main`` so the cleanup RULES can be tested directly. They were
    previously only reachable by running the script against the real
    ``datasets/repos``, which no test could do safely -- and a mutation run
    confirmed the consequence: replacing ``-fdxq`` with ``-fdq`` here, or dropping
    ``--ignored`` from the verification, left every test in the suite green while
    re-opening the exact leak this script is the documented remedy for.
    """
    head = git(repo, "rev-parse", "--verify", "HEAD")
    if head.returncode != 0:
        # Broken HEAD (observed after an interrupted checkout).
        return "broken"

    # Expire the reflog UNCONDITIONALLY, before the dirty check below.
    #
    # A reflog is invisible to `git status`, so a checkout whose ONLY problem is a
    # stale reflog reported "pristine" and was skipped entirely -- leaving it in
    # place. Measured: that is how an operator's name and email
    # (`agirahman <agi.rahman.s@gmail.com>`) survived in a dataset checkout.
    #
    # The reflog is NOT part of a pristine checkout: a fresh clone has no
    # logs/HEAD. Ours only exists because `reset_working_tree` runs `git reset`,
    # and git attributes the entry to the ambient git identity. So it is a trace
    # of OUR runs and it carries PII.
    #
    # NOTE: this MUST NOT move into reset_working_tree. `git reflog expire` writes
    # to the reflog itself, and the .git tamper check reads a reflog that shrank to
    # empty as tampering (measured: the next reset returned False). Cleaning here,
    # outside a run, avoids that false positive.
    git(repo, "reflog", "expire", "--expire=now", "--all")

    # --ignored, because plain `status --porcelain` hides .gitignore'd files.
    # A checkout whose ONLY problem is `__pycache__/` or `.pytest_cache/` used to
    # be reported as pristine and skipped entirely, so the cleanup never ran.
    dirty = git(repo, "status", "--porcelain", "--ignored").stdout.strip()
    if not dirty:
        return "pristine"

    git(repo, "reset", "-q")
    git(repo, "checkout", "--", ".")
    # -x so .gitignore'd leftovers are removed too. Without it the documented
    # remedy reported success while leaving the problem: an agent's .pyc
    # (recoverable via marshal.load) and pytest's .pytest_cache (which records
    # WHICH tests failed -- i.e. the bug location for FAIL_TO_PASS) both survived.
    git(repo, "clean", "-fdxq", ".")
    after = git(repo, "status", "--porcelain", "--ignored").stdout.strip()
    return "cleaned" if not after else "dirty"


def main() -> int:
    cleaned, broken, ok, dirty, skipped = 0, 0, 0, 0, 0
    for gitdir in sorted(REPO_BASE.rglob(".git")):
        if not is_instance_checkout(gitdir):
            # `datasets/repos/.git` is an empty decoy; cleaning "it" would operate
            # on the project repository. Skipped, and reported so it is visible.
            print(f"  SKIP (not an instance checkout): {gitdir.relative_to(ROOT)}")
            skipped += 1
            continue
        repo = gitdir.parent
        state = clean_one(repo)
        if state == "broken":
            print(f"  BROKEN HEAD: {repo.relative_to(ROOT)}")
            broken += 1
        elif state == "pristine":
            ok += 1
        elif state == "cleaned":
            print(f"  cleaned: {repo.relative_to(ROOT)}  -> clean")
            cleaned += 1
        else:
            print(f"  STILL DIRTY: {repo.relative_to(ROOT)}")
            dirty += 1

    print()
    print(f"  pristine: {ok}   cleaned: {cleaned}   broken HEAD: {broken}"
          + (f"   STILL DIRTY: {dirty}" if dirty else "")
          + (f"   skipped: {skipped}" if skipped else ""))
    # Non-zero when anything is still dirty, so this cannot silently "succeed"
    # while leaving contamination behind.
    return 1 if dirty else 0


if __name__ == "__main__":
    sys.exit(main())
