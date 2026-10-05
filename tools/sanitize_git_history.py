"""Sanitize git history in all dataset checkouts (timesafe sanitization).

Adopts git_clone_timesafe from SWE-bench PR #471:
- Strips all git remotes (prevents network access / fetch / pull from agent)
- Deletes ONLY future tags (commit timestamp > base_commit timestamp).
  Past tags are strictly preserved (e.g. pytest-5840 requires past tags).
- Deletes any local branches pointing to future commits.
- Expire reflogs and prune unreachable objects (gc --prune=now).
- Fail-closed assertion: git log --oneline --all --after=@<ts+1> MUST be 0.
- Verifies working tree remains clean.

Usage:
    python tools/sanitize_git_history.py           # dry-run (default)
    python tools/sanitize_git_history.py --apply   # apply sanitization
    python tools/sanitize_git_history.py --check   # read-only preflight gate check
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_BASE = ROOT / "datasets" / "repos"

DUMMY_SHAS = {"0" * 40, "1" * 40, "2" * 40}


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def is_sha40(name: str) -> bool:
    return len(name) == 40 and all(c in "0123456789abcdef" for c in name.lower())


def get_commit_timestamp(repo: Path, rev: str) -> int | None:
    res = run_git(repo, "show", "-s", "--format=%ct", rev)
    if res.returncode == 0 and res.stdout.strip():
        try:
            return int(res.stdout.strip())
        except ValueError:
            return None
    return None


def inspect_checkout(repo: Path) -> dict:
    """Inspect a checkout for git sanitization status."""
    base_commit = repo.name
    info = {
        "repo": repo,
        "base_commit": base_commit,
        "is_dummy": base_commit in DUMMY_SHAS,
        "broken_head": False,
        "base_ts": None,
        "remotes": [],
        "future_tags": [],
        "past_tags": [],
        "future_branches": [],
        "future_commits_count": 0,
        "working_tree_clean": True,
    }

    if info["is_dummy"]:
        return info

    head = run_git(repo, "rev-parse", "--verify", "HEAD")
    if head.returncode != 0:
        info["broken_head"] = True
        return info

    ts = get_commit_timestamp(repo, base_commit)
    if ts is None:
        ts = get_commit_timestamp(repo, "HEAD")
    info["base_ts"] = ts

    if ts is None:
        info["broken_head"] = True
        return info

    # Check remotes
    rem_out = run_git(repo, "remote").stdout.strip()
    info["remotes"] = [r.strip() for r in rem_out.splitlines() if r.strip()]

    # Check tags
    tags_out = run_git(repo, "tag", "-l").stdout.strip()
    tags = [t.strip() for t in tags_out.splitlines() if t.strip()]
    for tag in tags:
        tag_ts = get_commit_timestamp(repo, f"{tag}^{{commit}}")
        if tag_ts is not None:
            if tag_ts > ts:
                info["future_tags"].append(tag)
            else:
                info["past_tags"].append(tag)

    # Check local branches pointing to future commits
    branch_out = run_git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/").stdout.strip()
    branches = [b.strip() for b in branch_out.splitlines() if b.strip()]
    for branch in branches:
        branch_ts = get_commit_timestamp(repo, f"refs/heads/{branch}")
        if branch_ts is not None and branch_ts > ts:
            info["future_branches"].append(branch)

    # Check future commits across all refs
    after_ts = ts + 1
    log_out = run_git(repo, "log", "--oneline", "--all", f"--after=@{after_ts}").stdout.strip()
    lines = [c for c in log_out.splitlines() if c.strip()]
    info["future_commits_count"] = len(lines)

    status = run_git(repo, "status", "--porcelain").stdout.strip()
    info["working_tree_clean"] = len(status) == 0

    return info


def sanitize_checkout(repo: Path, info: dict) -> tuple[bool, str]:
    """Sanitize a checkout by removing remotes, future tags/branches, and pruning reflogs.

    Returns (success: bool, message: str).
    """
    ts = info["base_ts"]
    if ts is None:
        return False, "Cannot determine base commit timestamp"

    # 1. Remove remotes
    for remote in info["remotes"]:
        res = run_git(repo, "remote", "remove", remote)
        if res.returncode != 0:
            return False, f"Failed to remove remote {remote}: {res.stderr.strip()}"

    # 2. Delete future tags
    for tag in info["future_tags"]:
        res = run_git(repo, "tag", "-d", tag)
        if res.returncode != 0:
            return False, f"Failed to delete tag {tag}: {res.stderr.strip()}"

    # 3. Delete future branches
    for branch in info["future_branches"]:
        res = run_git(repo, "branch", "-D", branch)
        if res.returncode != 0:
            return False, f"Failed to delete branch {branch}: {res.stderr.strip()}"

    # 4. Expire reflog and garbage collect
    run_git(repo, "reflog", "expire", "--expire=now", "--all")
    gc_res = run_git(repo, "gc", "--prune=now")
    if gc_res.returncode != 0:
        return False, f"git gc failed: {gc_res.stderr.strip()}"

    # 5. Fail-closed assertion: git log --oneline --all --after=@<ts+1> must be 0
    after_ts = ts + 1
    verify_log = run_git(repo, "log", "--oneline", "--all", f"--after=@{after_ts}").stdout.strip()
    remaining = [c for c in verify_log.splitlines() if c.strip()]
    if remaining:
        return False, f"FAIL-CLOSED: {len(remaining)} commits remain after base_ts ({remaining[:3]})"

    # 6. Verify working tree status is still clean
    status = run_git(repo, "status", "--porcelain").stdout.strip()
    if status:
        return False, f"Working tree became dirty after sanitization: {status[:100]}"

    return True, f"Sanitized: removed {len(info['remotes'])} remote(s), {len(info['future_tags'])} future tag(s), {len(info['future_branches'])} future branch(es)"


def find_checkouts(base_dir: Path) -> list[Path]:
    """Find all git repository checkouts under base_dir."""
    repos = []
    for gitdir in sorted(base_dir.rglob(".git")):
        repo = gitdir.parent
        if is_sha40(repo.name):
            repos.append(repo)
    return repos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=False,
                       help="show what would be sanitized without modifying (default)")
    group.add_argument("--apply", action="store_true", default=False,
                       help="execute sanitization on checkouts")
    group.add_argument("--check", action="store_true", default=False,
                       help="read-only preflight gate: exit 0 if all clean, 1 if any leak")
    parser.add_argument("--repo-dir", default=str(REPO_BASE),
                        help="path to datasets/repos base directory")
    parser.add_argument("--repo", default=None,
                        help="target a specific checkout path instead of scanning repo-dir")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="print details for every checkout")

    args = parser.parse_args(argv)
    mode = "check" if args.check else ("apply" if args.apply else "dry-run")
    repo_dir = Path(args.repo_dir)

    if args.repo:
        target_repo = Path(args.repo)
        if not target_repo.is_absolute():
            target_repo = ROOT / target_repo
        if not (target_repo / ".git").exists():
            print(f"[error] repo path not a git repository: {target_repo}", file=sys.stderr)
            return 1
        checkouts = [target_repo]
        repo_dir = target_repo.parent
    else:
        if not repo_dir.exists():
            print(f"[error] repo dir not found: {repo_dir}", file=sys.stderr)
            return 1
        checkouts = find_checkouts(repo_dir)
    print(f"=== GIT SANITIZATION ({mode.upper()}) ===")
    print(f"Found {len(checkouts)} checkout(s) under {repo_dir.relative_to(ROOT) if repo_dir.is_relative_to(ROOT) else repo_dir}")

    clean_count = 0
    needs_action_count = 0
    sanitized_count = 0
    failed_count = 0
    skipped_count = 0

    for repo in checkouts:
        rel = repo.relative_to(repo_dir)
        info = inspect_checkout(repo)

        if info["is_dummy"] or info["broken_head"]:
            skipped_count += 1
            if args.verbose:
                print(f"  [SKIP] {rel} (dummy/broken)")
            continue

        needs_action = (
            len(info["remotes"]) > 0
            or len(info["future_tags"]) > 0
            or len(info["future_branches"]) > 0
            or info["future_commits_count"] > 0
        )

        if not needs_action:
            clean_count += 1
            if args.verbose:
                print(f"  [OK]   {rel} (clean, {len(info['past_tags'])} past tag(s) kept)")
            continue

        needs_action_count += 1
        summary = (
            f"remotes={len(info['remotes'])}, "
            f"future_tags={len(info['future_tags'])}, "
            f"future_branches={len(info['future_branches'])}, "
            f"future_commits={info['future_commits_count']}"
        )

        if mode == "check":
            print(f"  [LEAK] {rel}: {summary}")
            failed_count += 1
        elif mode == "dry-run":
            print(f"  [PLAN] {rel}: {summary}")
        elif mode == "apply":
            ok, msg = sanitize_checkout(repo, info)
            if ok:
                sanitized_count += 1
                print(f"  [DONE] {rel}: {msg}")
            else:
                failed_count += 1
                print(f"  [FAIL] {rel}: {msg}")

    print()
    print("--- SUMMARY ---")
    print(f"Total checkouts: {len(checkouts)}")
    print(f"Pristine/clean : {clean_count}")
    print(f"Skipped (dummy): {skipped_count}")

    if mode == "check":
        print(f"Leaks found    : {failed_count}")
        if failed_count > 0:
            print("GATE VERDICT   : FAILED (run tools/sanitize_git_history.py --apply to fix)")
            return 1
        print("GATE VERDICT   : PASSED (all checkouts time-safe and isolated)")
        return 0

    if mode == "dry-run":
        print(f"Need sanitize  : {needs_action_count}")
        print("Run with --apply to execute sanitization.")
        return 0

    if mode == "apply":
        print(f"Sanitized      : {sanitized_count}")
        print(f"Failed         : {failed_count}")
        return 1 if failed_count > 0 else 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
