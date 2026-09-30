"""Print an instance's GOLD PATCH, straight from the dataset.

The companion diagnostic (``tools/diagnose_dirty_state.py``) reports how much of a
dirty checkout matches the gold patch, and a ratio is only as good as the gold text
it compares against. This prints that text so the comparison can be checked by eye.

Why this matters: a dirty checkout is ambiguous. An agent's leftover edit and the
applied GOLD PATCH both show up as "modified tracked file" in ``git status``, but
they mean opposite things -- garbage to discard versus a solved tree that would make
the next run's diff meaningless.

Usage:
    python tools/show_gold_patch.py                       # the five django instances
    python tools/show_gold_patch.py django__django-11001
    python tools/show_gold_patch.py --all                 # every instance
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dataset_loader import load_swe_bench_lite  # noqa: E402

DEFAULT = [
    "django__django-10914",
    "django__django-10924",
    "django__django-11001",
    "django__django-11019",
    "django__django-11039",
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("instances", nargs="*", default=None)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    meta = {r["instance_id"]: r for r in load_swe_bench_lite()}

    if args.all:
        targets = sorted(meta)
    elif args.instances:
        targets = args.instances
    else:
        targets = DEFAULT

    for iid in targets:
        row = meta.get(iid)
        if row is None:
            print(f"\n{iid}: NOT IN DATASET")
            continue
        gold = row.get("patch", "") or ""
        files = [l for l in gold.splitlines() if l.startswith("diff --git")]
        added = [l for l in gold.splitlines()
                 if l.startswith("+") and not l.startswith("+++")]
        removed = [l for l in gold.splitlines()
                   if l.startswith("-") and not l.startswith("---")]

        print("=" * 78)
        print(f"  {iid}")
        print(f"  files: {len(files)}  |  +{len(added)} -{len(removed)}  |  "
              f"{len(gold)} bytes")
        print("=" * 78)
        print(gold)
        print()


if __name__ == "__main__":
    main()
