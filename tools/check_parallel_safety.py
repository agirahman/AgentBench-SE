"""Do the three target issues share a working directory?

WHY THIS DECIDES PARALLELISM
The repo cache layout is datasets/repos/<owner>/<repo>/<base_commit>, so two runs
collide only if they use the SAME base_commit. Three issues from the same repo can
still be parallel-safe if their commits differ, and must be serialised if they do
not -- reset_working_tree() on a shared directory would wipe the other run's edits
mid-flight and the captured diffs would be wrong.

An earlier attempt printed "all three have DISTINCT base_commits" while having
found ZERO issues: an empty set compares equal to another empty set. That is the
false-positive pattern this file avoids by asserting the data was actually found.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, "src")

TARGETS = ["django__django-11019", "django__django-11001", "django__django-10914"]

print("=" * 78)
print("BASE COMMITS OF THE TARGET ISSUES")
print("=" * 78)

try:
    from dataset_loader import load_swe_bench_lite
except Exception as e:  # noqa: BLE001
    sys.exit(f"cannot import dataset_loader: {e}")

rows = load_swe_bench_lite()
by_id = {d["instance_id"]: d for d in rows}
print(f"dataset loaded: {len(rows)} instances (SWE-bench Lite)")

found = {}
for iid in TARGETS:
    d = by_id.get(iid)
    if d is None:
        print(f"  {iid}: NOT IN DATASET")
        continue
    found[iid] = d["base_commit"]
    print(f"  {iid}: base_commit={d['base_commit'][:12]}  repo={d['repo']}")

if not found:
    sys.exit("\nno target issues found -- refusing to conclude anything")

print("\n" + "=" * 78)
print("VERDICT")
print("=" * 78)
commits = list(found.values())
unique = set(commits)
print(f"issues found      : {len(found)} of {len(TARGETS)}")
print(f"distinct commits  : {len(unique)} of {len(commits)}")

if len(unique) == len(commits):
    print("=> DISTINCT base_commits: each issue has its own working directory,")
    print("   so parallel runs on these three do not collide on the repo cache.")
else:
    dupes = {c: [i for i, x in found.items() if x == c] for c in unique if commits.count(c) > 1}
    print("=> COLLISION: these share a working directory and MUST run sequentially:")
    for commit, ids in dupes.items():
        print(f"     {commit[:12]}: {', '.join(ids)}")

# Also check the on-disk cache so the claim is grounded in the real layout.
cache = pathlib.Path("datasets/repos/django/django")
if cache.is_dir():
    on_disk = {p.name for p in cache.iterdir() if p.is_dir()}
    print(f"\non-disk checkouts under {cache}: {len(on_disk)}")
    for iid, commit in found.items():
        present = commit in on_disk
        print(f"  {iid}: {commit[:12]} {'present' if present else 'NOT CACHED'}")
else:
    print(f"\n(no cache dir at {cache})")
