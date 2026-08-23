#!/usr/bin/env python
"""Pre-fetch all instance repos into the local cache (no LLM tokens used).

Walks the selected SWE-bench Lite instances and calls
source_context.get_repo_at_commit() for each, which shallow-fetches
(git init + fetch --depth 1 + checkout) the exact base_commit into
datasets/repos/{repo}/{base_commit}. Cached repos are skipped.

Usage:
    python tools/prepare_repos.py            # fetch all missing
    python tools/prepare_repos.py --check    # only report status, no fetch
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from dataset_loader import select_issues  # noqa: E402
from source_context import get_repo_at_commit  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only report cache status; do not fetch.",
    )
    args = parser.parse_args()

    issues = select_issues()
    print(f"Instances selected: {len(issues)}")

    ok = fail = cached = 0
    failures: list[str] = []
    t0 = time.time()

    for idx, issue in enumerate(issues, 1):
        p = Path("datasets/repos") / issue.repo / issue.base_commit
        already = (p / ".git").exists()
        if already:
            cached += 1
            print(f"[{idx:>3}/{len(issues)}] CACHED  {issue.instance_id}")
            continue
        if args.check:
            print(f"[{idx:>3}/{len(issues)}] MISSING  {issue.instance_id}")
            continue
        print(f"[{idx:>3}/{len(issues)}] FETCHING {issue.instance_id} ({issue.repo}@{issue.base_commit[:8]})...", flush=True)
        try:
            path = get_repo_at_commit(issue.repo, issue.base_commit)
        except Exception as e:  # noqa: BLE001
            path = None
            print(f"      error: {e}")
        if path is not None:
            ok += 1
            print(f"      OK -> {path}")
        else:
            fail += 1
            failures.append(issue.instance_id)
            print("      FAILED")

    elapsed = time.time() - t0
    print("\n=== Summary ===")
    print(f"cached already : {cached}")
    print(f"fetched now    : {ok}")
    print(f"failed         : {fail}")
    print(f"elapsed        : {elapsed:.0f}s")
    if failures:
        print("failed instances:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
