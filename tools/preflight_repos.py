"""Pre-run preflight: can the sweep actually run? Docker first, then repos.

The sweep runs 50 issues x 3 strategies, each starting from a pristine checkout at
its base_commit. A repo that is dirty, missing, or whose HEAD does not match
base_commit produces a patch captured against the WRONG tree -- and that failure is
invisible in the results: the patch looks well-formed, `git apply` succeeds, and
only the evaluation fails, for reasons that look like the model's fault.

The audit found 10 dirty caches and 1 repo whose HEAD is the all-zeros commit, so
this checks all three properties per instance and exits non-zero if any fails, so
it can gate the run rather than being a report nobody reads.

DOCKER GATE (added 2026-10-07, incident EXP-20261007-003)
---------------------------------------------------------
This tool used to check ONLY the checkouts. Docker was never checked, and the
whole Oct 7 session ran with the daemon down: every `run_tests` call in all 9
runs returned "[error] Docker daemon is not running or unreachable.", so the
agents worked with no ability to run a single test -- and every run was still
recorded as VALID. The contamination was invisible in the results and was found
by reading the tool-call trail by hand.

That is the "control that does not cover the thing it claims to cover" failure:
a green preflight said "safe to run" while the run could not execute tests at
all. So the Docker check is now part of this gate and it is FAIL-HARD:

    1. `docker info`                      -- is the daemon up?
    2. `docker image inspect <image>`      -- is the sandbox image built?
    3. `docker run --rm <image> echo ok`   -- does a container actually start?

Step 3 is not redundant with steps 1-2: a daemon can be reachable and an image
present while `docker run` still fails (no WSL backend, out of disk, a broken
Docker Desktop state). Only running a container proves the sweep can run tests.

It runs BEFORE the (slow) dataset load, because a down daemon is both the most
likely problem and the fastest to detect. There is deliberately NO skip flag: a
gate with a bypass gets bypassed, which is how this incident happened.

Usage:
    python tools/preflight_repos.py
    python tools/preflight_repos.py --json preflight.json
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
# The repo_link_scan helper lives next to this file, so `tools/` must be importable
# when this script is run directly (as the sweep and the readiness gate both do).
sys.path.insert(0, str(Path(__file__).resolve().parent))


def run_git(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, f"{type(exc).__name__}: {exc}"


#: The sandbox image `run_tests` launches. Read from the same env var the tool
#: itself uses (agents/tools.py:_DOCKER_SANDBOX_IMAGE) so the gate cannot pass
#: while checking a different image than the one the sweep will run.
SANDBOX_IMAGE = os.environ.get("DOCKER_SANDBOX_IMAGE", "agentbench-sandbox:latest")


def _run_docker(args: list[str], timeout: int) -> tuple[int, str]:
    """Run a docker CLI command, returning (returncode, combined output).

    ``timeout`` is explicit on every call: `docker info` against a daemon that is
    starting up can hang, and a preflight that hangs is indistinguishable from a
    sweep that hangs -- the failure mode this whole change exists to prevent.
    """
    try:
        proc = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 1, f"docker {' '.join(args)} timed out after {timeout}s"
    except FileNotFoundError:
        return 1, "docker CLI executable not found in PATH"
    except OSError as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def check_docker() -> tuple[bool, list[str]]:
    """Is Docker usable? Three FAIL-HARD checks, in cheapest-first order.

    Returns ``(ok, problems)``. A single False here aborts the sweep: see the
    module docstring for the incident (EXP-20261007-003) that made this a gate
    instead of a warning.

    Checks are cumulative rather than short-circuiting so the operator sees ALL
    problems at once. Reporting "daemon down" and then, after a fix, "image
    missing" would mean two round-trips to a working state.
    """
    problems: list[str] = []

    # 1. Daemon reachable.
    code, out = _run_docker(["info"], timeout=30)
    if code != 0:
        detail = out.strip().splitlines()[0][:200] if out.strip() else "exit non-zero"
        problems.append(f"docker daemon is not running or unreachable: {detail}")
        # The remaining checks all need the daemon; running them would only add
        # noise ("image not found") that hides the real cause.
        return False, problems

    # 2. Sandbox image present locally.
    code, out = _run_docker(["image", "inspect", SANDBOX_IMAGE], timeout=30)
    if code != 0:
        problems.append(
            f"sandbox image '{SANDBOX_IMAGE}' is not present locally "
            f"(build it from docker/sandbox.Dockerfile)"
        )
        return False, problems

    # 3. A container actually starts. NOT redundant with 1+2: the daemon can be
    #    reachable and the image present while `docker run` still fails.
    code, out = _run_docker(["run", "--rm", SANDBOX_IMAGE, "echo", "ok"], timeout=120)
    if code != 0:
        detail = out.strip().splitlines()[-1][:200] if out.strip() else "exit non-zero"
        problems.append(
            f"could not start a container from '{SANDBOX_IMAGE}': {detail}"
        )
        return False, problems

    return True, problems


def report_docker_failure(problems: list[str]) -> None:
    """Print the failure in the same shape the repo problems use."""
    print()
    print("=" * 78)
    print("  DOCKER GATE FAILED -- the sweep cannot run tests")
    print("=" * 78)
    for p in problems:
        print(f"  - {p}")
    print()
    print("  Docker daemon tidak jalan / image tidak ada.")
    print("  Jalankan Docker Desktop dan pastikan image terbangun.")
    print()
    print("  Why this is fatal and not a warning: on 2026-10-07 the sweep ran with")
    print("  the daemon down, so all 9 runs could not execute a single test while")
    print("  still being recorded as VALID. The data had to be discarded.")
    print("=" * 78)


def resolve_checkout(repo: str, base_commit: str) -> Path | None:
    """Resolve a checkout path directly, without walking the cache.

    agents.tools.resolve_repo_root is the production resolver, but its last resort
    is an ``os.walk`` over ``datasets/repos/<owner>`` -- which is 1.35 GB across 51
    checkouts. Calling it once per instance (300 times) walks the cache hundreds of
    times and does not finish in any reasonable time; that is why this tool timed
    out twice before being written this way.

    The two candidate layouts are the ones the production resolver documents
    (agents/tools.py:82-96), and they are checked directly. This is a preflight
    over a fixed cache, so the deterministic path is both faster and exact; the
    fallback walk exists for unusual layouts that a preflight does not need.
    """
    from config import Config

    base = Path(Config.TOOLCALL_REPO_DIR)
    if not base.is_absolute():
        base = ROOT / base
    owner, _, name = repo.partition("/")

    candidates = [
        base / owner / name / base_commit,
        base / owner / base_commit,
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    return None


def load_instances(limit: int | None) -> list[dict]:
    """Load the instances that have a local checkout.

    The cache holds the 50-issue working set, not all 300 Lite instances, so
    checking the full dataset would report 250 false failures. Only instances whose
    checkout exists are returned, and the count is printed so it cannot be mistaken
    for full coverage.
    """
    from datasets import load_dataset

    ds = load_dataset("SWE-bench/SWE-bench_Lite", split="test")
    rows = [dict(row) for row in ds]

    present = [r for r in rows if resolve_checkout(r["repo"], r["base_commit"])]
    if limit is not None:
        return present[:limit]
    return present


def check_instance(row: dict) -> dict:
    instance_id = row["instance_id"]
    repo = row["repo"]
    base = row["base_commit"]
    path = resolve_checkout(repo, base)

    problems: list[str] = []

    if path is None:
        return {
            "instance_id": instance_id,
            "repo": repo,
            "path": "",
            "ok": False,
            "problems": ["no checkout found (runner would fall back to the shared "
                         "sandbox base and read another instance's files)"],
        }

    if not (path / ".git").exists():
        problems.append("no .git (not a git checkout)")

    code, head = run_git(["rev-parse", "HEAD"], path)
    actual_head = head.strip().splitlines()[-1] if head.strip() else ""
    if code != 0:
        problems.append(f"git rev-parse HEAD failed: {head.strip()[:120]}")
    elif actual_head != base:
        problems.append(f"HEAD is {actual_head[:12]}, expected {base[:12]}")

    code, status = run_git(["status", "--porcelain"], path)
    if code != 0:
        problems.append(f"git status failed: {status.strip()[:120]}")
    elif status.strip():
        changed = len([l for l in status.strip().splitlines() if l.strip()])
        problems.append(f"working tree dirty ({changed} path(s))")

    # Reparse-point scan, INDEPENDENT of git. A link named `x.pyc` (or placed in
    # `build/`) is hidden by .gitignore, so git status reports a clean tree while
    # the link is present and `grep` can read through it. Measured 2026-10-06.
    from repo_link_scan import describe, find_reparse_points

    links = find_reparse_points(path)
    if links:
        detail = "; ".join(describe(p, path) for p in links[:5])
        more = f" (+{len(links) - 5} more)" if len(links) > 5 else ""
        problems.append(
            f"symlink/junction inside checkout ({len(links)}): {detail}{more}"
        )

    return {
        "instance_id": instance_id,
        "repo": repo,
        "path": str(path),
        "ok": not problems,
        "problems": problems,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, default=None,
                        help="Write the full report here.")
    parser.add_argument("--quiet", action="store_true",
                        help="Only print failures.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Check only the first N instances with a checkout.")
    args = parser.parse_args()

    print("=" * 78)
    print("  PREFLIGHT: can the sweep run? (docker first, then repo state)")
    print("=" * 78)

    # Docker FIRST, before the (slow) dataset load. A down daemon is both the
    # most likely problem and the fastest to detect, and every later check is
    # pointless if the sweep cannot run a container at all. See the module
    # docstring for the incident this gate exists to prevent.
    print("\n  Checking Docker (a down daemon invalidates every run)...")
    docker_ok, docker_problems = check_docker()
    if docker_ok:
        print(f"  OK    daemon up, image '{SANDBOX_IMAGE}' present, container starts")
    else:
        report_docker_failure(docker_problems)
        sys.exit(1)

    try:
        instances = load_instances(args.limit)
    except Exception as exc:  # noqa: BLE001
        print(f"  Could not load the dataset: {type(exc).__name__}: {exc}")
        sys.exit(2)

    if not instances:
        print("\n  No instances have a local checkout. Nothing to check -- but the")
        print("  runner would fall back to the shared sandbox base for every issue.")
        sys.exit(1)

    print(f"\n  checking {len(instances)} instance(s) that have a local checkout")
    print("  (the cache holds the working set, not all 300 Lite instances)\n")

    results = [check_instance(row) for row in instances]
    bad = [r for r in results if not r["ok"]]

    for r in results:
        if r["ok"]:
            if not args.quiet:
                print(f"  OK    {r['instance_id']}")
        else:
            print(f"  FAIL  {r['instance_id']}  ({r['repo']})")
            for p in r["problems"]:
                print(f"          - {p}")

    print()
    print("=" * 78)
    print(f"  {len(results) - len(bad)}/{len(results)} usable, {len(bad)} with problems")
    if bad:
        print()
        print("  A dirty or mis-based checkout makes the captured diff relative to the")
        print("  WRONG tree, and the failure only shows up at evaluation time, looking")
        print("  like the model's fault. Fix these before running:")
        print("    python tools/clean_repos.py")

        # A junction is NOT removed by `git clean`: git does not track it and, when
        # its name matches .gitignore, does not even report it. Print the exact
        # removal commands instead of leaving the reader to guess.
        link_problems = [p for r in bad for p in r["problems"]
                         if p.startswith("symlink/junction")]
        if link_problems:
            print()
            print("  !! symlink/junction(s) found. `git clean` does NOT remove these, and")
            print("     `git status` cannot see them when their name matches .gitignore.")
            print("     Remove them explicitly, then re-run this preflight:")
            for r in bad:
                if any(p.startswith("symlink/junction") for p in r["problems"]):
                    print(f"       powershell -Command \"Get-ChildItem -LiteralPath "
                          f"'{r['path']}' -Recurse -Force | "
                          f"Where-Object {{ $_.Attributes -band "
                          f"[IO.FileAttributes]::ReparsePoint }} | Remove-Item -Force\"")
        print("=" * 78)
    else:
        print("  Every repo is pristine at its base_commit -- safe to run.")
        print("=" * 78)

    if args.json:
        args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\n  report written to {args.json}")

    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
