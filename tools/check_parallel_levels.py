"""Can the three budget levels run in PARALLEL, or must they be sequential?

The question is not "are the issues independent" (they are) but "do two runs at
different budgets touch the same files".

Within ONE run the three target issues are parallel-safe: they have distinct
base_commits (verified in tools/check_parallel_safety.py), so each gets its own
working directory under datasets/repos/django/django/<commit>.

Across the THREE BUDGET LEVELS the situation inverts. All three levels run the
SAME three issues, hence the SAME base_commits, hence the SAME working
directories. The strategies call reset_working_tree() before each act to get a
pristine checkout -- so a level-100 run mid-edit would have its files wiped by a
level-200 run, and both captured diffs would be wrong. Silent corruption, not a
crash.

This measures the cost of the fix: giving each level its own repo cache via
REPO_CACHE_DIR. If the copies are small the parallel plan is cheap; if they are
large, sequential is the honest answer.
"""
from __future__ import annotations

import pathlib
import shutil

CACHE = pathlib.Path("datasets/repos")
TARGETS = ["93e892bb645b16ebaf287beb5fe7f3ffe8d10408",
           "ef082ebb84f00e38af4e8880d04e8365c2766d34",
           "e7fd69d051eaa67cb17f172a39b57253e9cb831a"]

print("=" * 78)
print("COST OF ISOLATING EACH BUDGET LEVEL (separate REPO_CACHE_DIR)")
print("=" * 78)

total = 0
for commit in TARGETS:
    d = CACHE / "django" / "django" / commit
    if not d.is_dir():
        print(f"  {commit[:12]}: MISSING")
        continue
    size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
    total += size
    print(f"  {commit[:12]}: {size / 1e6:8.1f} MB")

print(f"\n  one level's three checkouts : {total / 1e6:.1f} MB")
print(f"  three levels (3 copies)     : {3 * total / 1e6:.1f} MB")

free = shutil.disk_usage(".").free
print(f"  free disk space             : {free / 1e9:.1f} GB")
print(f"  verdict                     : "
      f"{'fits comfortably' if 3 * total < free * 0.1 else 'TIGHT -- check disk'}")

print("""
"3 copies" counts the 2 extra levels; the first level can keep using the existing
cache. Copying three django checkouts is the only setup cost, and it is one-time.

IMPORTANT: a copy must preserve .git, because reset_working_tree() runs
`git checkout -- .` / `git clean` inside it. A plain file copy is fine as long as
the .git directory comes along (it does -- the loop above counts it).""")

print("=" * 78)
print("THE OTHER SHARED RESOURCE: THE PROVIDER")
print("=" * 78)
print("""
Beyond the repo cache, three parallel runs share:
  - results/experiment_index.json  -> protected by an O_EXCL lock with stale-lock
    breaking (src/experiment_id.py), verified by test_concurrent_ids_are_unique.
    Safe.
  - results/<EXP-id>/              -> one directory per run. Safe.
  - 9router at localhost:20128     -> measured by the parallel-readiness workstream
    to be stable at 8 concurrent requests. Three processes is well inside that.
  - datasets/repos/...             -> THE CONFLICT, described above.

So the provider and the result bookkeeping are already parallel-safe; only the
repo cache needs isolating.""")
