"""List dataset instances whose checkout is already cached locally."""

import sys
from pathlib import Path

ROOT = Path("D:/development/Skripsi2/AgantBech-SE")
sys.path.insert(0, str(ROOT / "src"))

from dataset_loader import load_swe_bench_lite  # noqa: E402

REPO_BASE = ROOT / "datasets" / "repos"

rows = load_swe_bench_lite()
print(f"dataset rows: {len(rows)}")
print()

cached, missing = [], []
for r in rows:
    iid = r["instance_id"]
    repo = r.get("repo", "")
    base = r.get("base_commit", "")
    owner, _, name = repo.partition("/")
    cands = [REPO_BASE / owner / name / base, REPO_BASE / owner / base]
    hit = next((c for c in cands if c.is_dir()), None)
    (cached if hit else missing).append((iid, repo, base))

print(f"cached: {len(cached)}    not cached: {len(missing)}")
print()
print("cached instances (first 20):")
for iid, repo, base in cached[:20]:
    print(f"  {iid:34} {repo:22} {base[:10]}")
