"""Pre-run preflight: is every repo the sweep needs actually usable?

The sweep runs 50 issues x 3 strategies, each starting from a pristine checkout at
its base_commit. A repo that is dirty, missing, or whose HEAD does not match
base_commit produces a patch captured against the WRONG tree -- and that failure is
invisible in the results: the patch looks well-formed, `git apply` succeeds, and
only the evaluation fails, for reasons that look like the model's fault.

The audit found 10 dirty caches and 1 repo whose HEAD is the all-zeros commit, so
this checks all three properties per instance and exits non-zero if any fails, so
it can gate the run rather than being a report nobody reads.

Usage:
    python tools/preflight_repos.py
    python tools/preflight_repos.py --json preflight.json
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


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
    print("  PREFLIGHT: repo state for the instances the sweep will run")
    print("=" * 78)

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
