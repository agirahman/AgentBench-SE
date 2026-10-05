"""Gate check: no symlink/junction inside any repo checkout.

Standalone command so `readiness_report.py` can run it as one check. Exits
non-zero (fail-closed) when a link is found, because a link inside a checkout
lets the host-side `grep` read through it and return files from outside the repo
— which is how the project's `.env` and a stored `model_patch` were leaked.

This is deliberately independent of `git status`: a link named `x.pyc`, or one
placed in `build/`, matches the checkout's .gitignore and is invisible to git
while remaining fully readable. See tools/repo_link_scan.py for the measurements.

Usage:
    python tools/scan_repo_links.py            # scan datasets/repos
    python tools/scan_repo_links.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from repo_link_scan import describe, find_reparse_points

REPO_BASE = ROOT / "datasets" / "repos"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, default=None,
                        help="Write the full report here.")
    parser.add_argument("--repo-dir", default=str(REPO_BASE),
                        help="directory holding the checkouts")
    args = parser.parse_args()

    base = Path(args.repo_dir)
    print("=" * 78)
    print("  REPO LINK SCAN: symlinks/junctions inside checkouts")
    print("=" * 78)

    if not base.is_dir():
        print(f"  [error] repo dir not found: {base}")
        return 1

    checkouts = sorted(p for p in base.rglob("*") if p.is_dir() and (p / ".git").exists())
    offenders: list[dict] = []

    for repo in checkouts:
        links = find_reparse_points(repo)
        if links:
            offenders.append({
                "repo": str(repo.relative_to(base)),
                "links": [describe(p, repo) for p in links],
                "paths": [str(p) for p in links],
            })

    if offenders:
        print()
        for off in offenders:
            print(f"  FAIL  {off['repo']}  ({len(off['links'])} link(s))")
            for desc in off["links"][:10]:
                print(f"          - {desc}")
            if len(off["links"]) > 10:
                print(f"          ... and {len(off['links']) - 10} more")
        print()
        print("  These are NOT removable with `git clean`: git does not track them and")
        print("  cannot see them when their name matches .gitignore. Remove explicitly:")
        print()
        for off in offenders:
            print(f"    powershell -Command \"Get-ChildItem -LiteralPath "
                  f"'{ROOT / 'datasets' / 'repos' / off['repo']}' -Recurse -Force | "
                  f"Where-Object {{ $_.Attributes -band "
                  f"[IO.FileAttributes]::ReparsePoint }} | Remove-Item -Force\"")
        print()
        print("=" * 78)
        print(f"  {len(checkouts) - len(offenders)}/{len(checkouts)} checkout(s) clean, "
              f"{len(offenders)} with links")
        print("  GATE VERDICT: FAILED")
        print("=" * 78)
        if args.json:
            args.json.write_text(json.dumps(offenders, indent=2), encoding="utf-8")
        return 1

    print(f"  {len(checkouts)} checkout(s) scanned, 0 links found.")
    print("  GATE VERDICT: PASSED")
    print("=" * 78)
    if args.json:
        args.json.write_text("[]", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
