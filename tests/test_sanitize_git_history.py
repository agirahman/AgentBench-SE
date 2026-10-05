"""Tests for time-safe git history sanitization (SWE-bench PR #471 pattern)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools.sanitize_git_history import (
    find_checkouts,
    get_commit_timestamp,
    inspect_checkout,
    main,
    run_git,
    sanitize_checkout,
)


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init")
    run_git(path, "config", "user.name", "Tester")
    run_git(path, "config", "user.email", "tester@example.com")


def _commit(path: Path, msg: str, date: str) -> str:
    f = path / f"{msg.replace(' ', '_')}.txt"
    f.write_text(msg, encoding="utf-8")
    run_git(path, "add", ".")
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = date
    env["GIT_COMMITTER_DATE"] = date
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", msg],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    res = run_git(path, "rev-parse", "HEAD")
    return res.stdout.strip()


def test_sanitize_removes_future_tags_keeps_past_tags(tmp_path):
    repo = tmp_path / ("a" * 40)
    _init_git_repo(repo)

    # 1. Past commit & tag (timestamp 1000)
    _commit(repo, "past commit", "2020-01-01T00:00:00Z")
    run_git(repo, "tag", "v0.9.0")

    # 2. Base commit (timestamp 2000)
    base_sha = _commit(repo, "base commit", "2020-01-02T00:00:00Z")

    # 3. Future commit & tag (timestamp 3000)
    _commit(repo, "future commit", "2020-01-03T00:00:00Z")
    run_git(repo, "tag", "v2.0.0")

    # Reset HEAD back to base commit
    run_git(repo, "reset", "--hard", base_sha)

    # Add a dummy remote
    run_git(repo, "remote", "add", "origin", "https://github.com/example/fake.git")

    info = inspect_checkout(repo)
    assert len(info["remotes"]) == 1
    assert "v2.0.0" in info["future_tags"]
    assert "v0.9.0" in info["past_tags"]

    ok, msg = sanitize_checkout(repo, info)
    assert ok, f"sanitize failed: {msg}"

    # Verify past tag remains, future tag is deleted
    tags = run_git(repo, "tag", "-l").stdout.splitlines()
    assert "v0.9.0" in tags, "past tag v0.9.0 must be preserved"
    assert "v2.0.0" not in tags, "future tag v2.0.0 must be deleted"

    # Verify remote is removed
    remotes = run_git(repo, "remote").stdout.splitlines()
    assert not remotes, "remotes must be removed"

    # Verify zero future commits
    after_ts = info["base_ts"] + 1
    after_log = run_git(repo, "log", "--oneline", "--all", f"--after=@{after_ts}").stdout.strip()
    assert not after_log, f"commits remain after base_ts: {after_log}"


def test_fail_closed_if_future_commits_cannot_be_pruned(tmp_path, monkeypatch):
    repo = tmp_path / ("b" * 40)
    _init_git_repo(repo)
    base_sha = _commit(repo, "base commit", "2020-01-02T00:00:00Z")

    info = inspect_checkout(repo)

    # Simulate a bug where run_git fails to actually prune or verify log reports a commit
    orig_run_git = run_git

    def fake_run_git(r, *args):
        if args and args[0] == "log" and "--after" in " ".join(args):
            # Simulate a future commit remaining
            proc = subprocess.CompletedProcess(["git", *args], 0)
            proc.stdout = "deadbeef Future leaked commit\n"
            proc.stderr = ""
            return proc
        return orig_run_git(r, *args)

    monkeypatch.setattr("tools.sanitize_git_history.run_git", fake_run_git)

    ok, msg = sanitize_checkout(repo, info)
    assert not ok, "sanitize must fail closed if future commits remain"
    assert "FAIL-CLOSED" in msg


def test_check_mode_reports_leak_and_returns_exit_1(tmp_path):
    repos_dir = tmp_path / "repos" / "owner" / "repo"
    repo = repos_dir / ("c" * 40)
    _init_git_repo(repo)
    _commit(repo, "base commit", "2020-01-02T00:00:00Z")
    run_git(repo, "remote", "add", "origin", "https://example.com/fake.git")

    rc = main(["--check", "--repo-dir", str(tmp_path / "repos")])
    assert rc == 1, "check mode must exit 1 when any repo has remotes/future refs"


def test_dry_run_mode_does_not_mutate(tmp_path):
    repos_dir = tmp_path / "repos" / "owner" / "repo"
    repo = repos_dir / ("d" * 40)
    _init_git_repo(repo)
    _commit(repo, "base commit", "2020-01-02T00:00:00Z")
    run_git(repo, "remote", "add", "origin", "https://example.com/fake.git")

    rc = main(["--dry-run", "--repo-dir", str(tmp_path / "repos")])
    assert rc == 0
    remotes = run_git(repo, "remote").stdout.strip()
    assert remotes == "origin", "dry-run must not mutate remotes"


def test_apply_mode_sanitizes_and_then_check_passes(tmp_path):
    repos_dir = tmp_path / "repos" / "owner" / "repo"
    repo = repos_dir / ("e" * 40)
    _init_git_repo(repo)
    base_sha = _commit(repo, "base commit", "2020-01-02T00:00:00Z")
    _commit(repo, "future commit", "2020-01-05T00:00:00Z")
    run_git(repo, "tag", "vFuture")
    run_git(repo, "reset", "--hard", base_sha)
    run_git(repo, "remote", "add", "origin", "https://example.com/fake.git")

    # Apply
    rc_apply = main(["--apply", "--repo-dir", str(tmp_path / "repos")])
    assert rc_apply == 0

    # Check
    rc_check = main(["--check", "--repo-dir", str(tmp_path / "repos")])
    assert rc_check == 0, "check mode must pass after apply sanitization"
