"""Is the dirty state an AGENT LEFTOVER, or the GOLD PATCH applied?

These look identical to `git status` -- both are modifications to tracked files --
but they mean opposite things:

* an agent leftover is garbage that would corrupt the next run's diff;
* the gold patch is the reference solution, and if it is sitting in the checkout
  then the previous run was NOT an agent run, and every diff captured against it
  is relative to the solved tree.

The distinguishing test is to compare the dirty diff against the instance's gold
patch from the dataset. Nothing is written here.

Usage:
    python tools/diagnose_dirty_state.py
"""
from __future__ import annotations

import difflib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import Config  # noqa: E402


def _git(root: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "") + (p.stderr or "")


def _added_lines(diff_text: str) -> list[str]:
    """The '+' lines, stripped of the marker -- the substance of a change."""
    out = []
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:].strip())
    return [l for l in out if l]


def _gold_added_lines(patch: str) -> list[str]:
    return _added_lines(patch)


def main() -> None:
    from datasets import load_dataset

    base = Path(Config.TOOLCALL_REPO_DIR)
    if not base.is_absolute():
        base = ROOT / base

    ds = load_dataset("SWE-bench/SWE-bench_Lite", split="test")
    rows = {r["instance_id"]: r for r in ds}

    dirty_instances = []
    for iid, row in sorted(rows.items()):
        owner, _, name = row["repo"].partition("/")
        root = base / owner / name / row["base_commit"]
        if not root.is_dir():
            root = base / owner / row["base_commit"]
        if not root.is_dir():
            continue
        status = _git(root, "status", "--porcelain")
        if any(l.strip() for l in status.splitlines()):
            dirty_instances.append((iid, root, row))

    print("=" * 78)
    print(f"  {len(dirty_instances)} dirty checkout(s)")
    print("=" * 78)

    for iid, root, row in dirty_instances:
        local = _git(root, "diff", "--no-color")
        gold = row.get("patch") or ""
        local_added = _added_lines(local)
        gold_added = _gold_added_lines(gold)

        # How much of the local change appears in the gold patch?
        overlap = [l for l in local_added if l in gold_added]
        ratio = len(overlap) / len(local_added) if local_added else 0.0

        print(f"\n  {iid}")
        print(f"    changed files   : {len(re.findall(r'^diff --git', local, re.M))}")
        print(f"    added lines     : {len(local_added)}")
        print(f"    gold patch lines: {len(gold_added)}")
        print(f"    MATCH with gold : {len(overlap)}/{len(local_added)} ({ratio:.0%})")

        if ratio >= 0.6:
            verdict = ("GOLD PATCH IS APPLIED -- this checkout is SOLVED, not dirty. "
                       "An agent run here would diff against the solution.")
        elif ratio > 0:
            verdict = ("PARTIAL overlap -- possibly an agent's attempt that "
                       "coincidentally matches part of the gold patch.")
        else:
            verdict = "NO overlap with gold -- looks like an agent leftover."

        print(f"    VERDICT         : {verdict}")

        # Show the added lines so the call can be checked by eye.
        print(f"    local additions :")
        for line in local_added[:8]:
            print(f"        {line[:88]}")
        if len(local_added) > 8:
            print(f"        ... and {len(local_added) - 8} more")

    print()
    print("=" * 78)
    print("  Nothing was modified. To clean, the correct operation is")
    print("  `git checkout -- . && git clean -fd` INSIDE the checkout (restores")
    print("  tracked files from git), NOT deleting the directory.")
    print("=" * 78)


if __name__ == "__main__":
    main()
