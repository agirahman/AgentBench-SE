"""Regression probe for sandbox escape vectors.

Tests each known escape vector against the sandbox defenses:
1. Python urllib download (e.g. Django 3.0 file from GitHub) -> must be blocked
2. Python socket connection (example.com) -> must be blocked
3. Command filter: git fetch -> must be blocked
4. Git future tag lookup: git show v2.8.0:requests/adapters.py in requests checkout -> must fail
5. Git future commit log: git log --all --after in requests checkout -> 0 lines
6. Command filter: pip download django -> must be blocked

Prints a summary table with PASS/FAIL per probe. Exits non-zero if ANY probe escapes.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.tools import run_tests, set_repo_root


def probe_1_urllib(repo: Path) -> tuple[bool, str]:
    """Test Python urllib.request download via run_tests."""
    cmd = (
        'python -c "import urllib.request; '
        "urllib.request.urlopen('https://raw.githubusercontent.com/django/django/3.0/django/forms/widgets.py', timeout=5)\""
    )
    res = run_tests(cmd)
    blocked = "[blocked]" in res or "PermissionError" in res
    return blocked, res.strip().splitlines()[-1] if res.strip() else ""


def probe_2_socket(repo: Path) -> tuple[bool, str]:
    """Test Python socket connection via run_tests."""
    cmd = 'python -c "import socket; socket.create_connection((\'example.com\', 443), timeout=5)"'
    res = run_tests(cmd)
    blocked = "[blocked]" in res or "PermissionError" in res
    return blocked, res.strip().splitlines()[-1] if res.strip() else ""


def probe_3_git_fetch(repo: Path) -> tuple[bool, str]:
    """Test git fetch command filtering via run_tests."""
    res = run_tests("git fetch origin")
    blocked = res.startswith("[blocked]")
    return blocked, res.strip()


def probe_4_future_tag(repo: Path) -> tuple[bool, str]:
    """Test that future tag v2.8.0 is gone in requests checkout."""
    if not repo.exists():
        return False, f"repo not found: {repo}"
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", "v2.8.0:requests/adapters.py"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # Must fail because tag does not exist
    blocked = proc.returncode != 0
    msg = (proc.stderr or proc.stdout).strip().splitlines()[0] if (proc.stderr or proc.stdout).strip() else f"exit {proc.returncode}"
    return blocked, msg


def probe_5_future_commits(repo: Path) -> tuple[bool, str]:
    """Test that git log --all after base_commit ts returns 0 commits in requests checkout."""
    if not repo.exists():
        return False, f"repo not found: {repo}"
    base_commit = repo.name
    ts_res = subprocess.run(
        ["git", "-C", str(repo), "show", "-s", "--format=%ct", base_commit],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if ts_res.returncode != 0:
        return False, f"cannot get ts for {base_commit}"
    ts = int(ts_res.stdout.strip())
    after_ts = ts + 1
    log_res = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline", "--all", f"--after=@{after_ts}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    commits = [c for c in log_res.stdout.splitlines() if c.strip()]
    blocked = len(commits) == 0
    return blocked, f"{len(commits)} commit(s) after base_commit"


def probe_6_pip_download(repo: Path) -> tuple[bool, str]:
    """Test pip download command filtering via run_tests."""
    res = run_tests("pip download django")
    blocked = res.startswith("[blocked]")
    return blocked, res.strip()


def main() -> int:
    req_dir = ROOT / "datasets" / "repos" / "psf" / "requests" / "0be38a0c37c59c4b66ce908731da15b401655113"
    set_repo_root(req_dir)

    probes = [
        ("1. urllib python download", probe_1_urllib),
        ("2. socket python connection", probe_2_socket),
        ("3. command: git fetch", probe_3_git_fetch),
        ("4. git future tag lookup", probe_4_future_tag),
        ("5. git log future commits", probe_5_future_commits),
        ("6. command: pip download", probe_6_pip_download),
    ]

    print("=" * 78)
    print("  SANDBOX ESCAPE REGRESSION PROBES")
    print("=" * 78)

    passed_all = True
    for name, fn in probes:
        ok, detail = fn(req_dir)
        status = "PASS (BLOCKED)" if ok else "FAIL (ESCAPED)"
        if not ok:
            passed_all = False
        print(f"  [{status:<14}] {name:<30} | {detail[:40]}")

    print("=" * 78)
    if passed_all:
        print("RESULT: ALL ESCAPE PROBES BLOCKED (PASS)")
        return 0
    print("RESULT: SOME PROBES ESCAPED DEFENSES (FAIL)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
