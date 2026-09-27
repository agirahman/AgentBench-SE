"""Confirm which regex flag the upstream (gold) fix used, for django-11001.

The claim under test: direct/planning resolved with re.DOTALL while review failed
with re.MULTILINE, so DOTALL is the correct flag. The authoritative check is the
instance's gold `patch` field in the dataset, not our inference from outcomes.
Also prints the gold patch for django-10924, where review's `_path_callable or
self.path` logic failed.
"""

import sys
from pathlib import Path

ROOT = Path("D:/development/Skripsi2/AgantBech-SE")
sys.path.insert(0, str(ROOT / "src"))
out = Path(sys.argv[1])

from dataset_loader import load_swe_bench_lite  # noqa: E402

meta = {r["instance_id"]: r for r in load_swe_bench_lite()}

_buf = []
def emit(s=""): _buf.append(s)

for iid in ("django__django-11001", "django__django-10924"):
    row = meta.get(iid, {})
    gold = row.get("patch", "") or ""
    emit(f"########## {iid}  GOLD PATCH ({len(gold)} bytes) ##########")
    for ln in gold.splitlines():
        if ln.startswith(("@@", "+", "-")) and not ln.startswith(("+++", "---")):
            emit(f"  {ln[:150]}")
        elif ln.startswith("diff --git"):
            emit(f"  {ln[:150]}")
    emit()

out.write_text("\n".join(_buf) + "\n", encoding="utf-8")
print("ok")
