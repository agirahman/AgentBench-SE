"""Why are these checkouts dirty, and is the dirt SAFE to discard?

``tools/preflight_repos.py`` fails a checkout whose working tree is dirty, because
a diff captured against a dirty tree is relative to the WRONG base -- the failure
only surfaces at evaluation time and looks like the model's fault.

But "dirty" is not one condition, and the fix differs per case. Before discarding
anything this reports, per instance:

* which paths differ,
* whether the change is a modification, a deletion, or an untracked file,
* whether the difference is COMMITTED content the checkout needs (which would mean
  the checkout is at the wrong base commit, not merely dirty),
* and how large the diff is, so an agent's leftover edit is distinguishable from a
  repo that was prepared wrong.

Nothing is written or removed by this script. It only reports.

Usage:
    python tools/inspect_dirty_repos.py
    python tools/inspect_dirty_repos.py --instance django__django-10914
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import Config  # noqa: E402


def _repo_base() -> Path:
    base = Path(Config.TOOLCALL_REPO_DIR)
    return base if base.is_absolute() else ROOT / base


def _git(root: Path, *args: str) -> str:
    p = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return (p.stdout or "") + (p.stderr or "")


def checkout_for(instance_id: str) -> Path | None:
    """Find the local checkout for an instance without walking the whole tree."""
    from datasets import load_dataset

    base = _repo_base()
    for row in load_dataset("SWE-bench/SWE-bench_Lite", split="test"):
        if row["instance_id"] != instance_id:
            continue
        owner, _, name = row["repo"].partition("/")
        for candidate in (base / owner / name / row["base_commit"],
                          base / owner / row["base_commit"]):
            if candidate.is_dir():
                return candidate
        return None
    return None


def report(instance_id: str) -> bool:
    root = checkout_for(instance_id)
    if root is None:
        print(f"\n  {instance_id}: no checkout found")
        return False

    print(f"\n{'=' * 76}")
    print(f"  {instance_id}")
    print(f"  {root}")
    print(f"{'=' * 76}")

    head = _git(root, "rev-parse", "HEAD").strip()
    print(f"  HEAD        : {head[:12]}")

    status = _git(root, "status", "--porcelain")
    entries = [l for l in status.splitlines() if l.strip()]
    if not entries:
        print("  status      : CLEAN")
        return False

    print(f"  dirty paths : {len(entries)}")
    print()
    print(f"  {'code':<4} {'path':<52} interpretation")
    print(f"  {'-' * 74}")
    for line in entries:
        code, _, path = line[:2], line[2], line[3:]
        if code == "??":
            interp = "UNTRACKED (new file, not in git)"
        elif code.strip() == "M":
            interp = "MODIFIED tracked file"
        elif code.strip() == "D":
            interp = "DELETED tracked file"
        elif code.strip() == "A":
            interp = "ADDED to index"
        else:
            interp = f"other ({code!r})"
        print(f"  {code:<4} {path:<52} {interpretation(interp, path)}")

    # How big is the change? An agent's leftover edit is usually small and in the
    # files the instance is about; a mis-based checkout shows large or unrelated
    # diffs.
    stat = _git(root, "diff", "--stat")
    if stat.strip():
        print()
        print("  diff --stat (tracked changes):")
        for line in stat.strip().splitlines():
            print(f"      {line}")

    return True


def interpretation(interp: str, path: str) -> str:
    """Flag the cases that are NOT safe to discard blindly."""
    if interp.startswith("UNTRACKED"):
        if path.endswith(".pyc") or "__pycache__" in path:
            return "build artifact -- safe to discard"
        return "untracked file -- check contents before discarding"
    return interp


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--instance", default=None)
    args = ap.parse_args()

    if args.instance:
        instances = [args.instance]
    else:
        from datasets import load_dataset

        ds = load_dataset("SWE-bench/SWE-bench_Lite", split="test")
        base = _repo_base()
        instances = []
        for row in sorted(ds, key=lambda r: r["instance_id"]):
            owner, _, name = row["repo"].partition("/")
            if ((base / owner / name / row["base_commit"]).is_dir()
                    or (base / owner / row["base_commit"]).is_dir()):
                instances.append(row["instance_id"])

    dirty = [i for i in instances if report(i)]
    print()
    print("=" * 76)
    print(f"  {len(dirty)} dirty checkout(s) of {len(instances)} inspected")
    if dirty:
        print()
        print("  NOTHING was modified by this script. To clean a checkout, use")
        print("  tools/reset_checkout.py, which runs git checkout/clean -- not a")
        print("  directory delete -- so tracked content is restored from git")
        print("  rather than removed.")
    print("=" * 76)


if __name__ == "__main__":
    main()
