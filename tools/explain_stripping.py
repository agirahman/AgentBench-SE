"""Why did test files survive stripping? Compare gold test_patch to model patches.

Stripping removes exactly the files named in the instance's gold `test_patch`.
If a model patch still touches a *different* test file, that file was not in the
gold patch — which raises the real question: will the harness's
`git checkout <base_commit> <test_files>` collide with it anyway?

The harness resets only the files listed in the gold test_patch, so a model
touching a different test file is not a collision risk. This script prints the
gold test-file list next to each model patch's file list so that claim can be
checked rather than assumed.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dataset_loader import load_swe_bench_lite  # noqa: E402
from experiments.swebench_adapter import collect_test_files  # noqa: E402


def files_in_patch(text: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"^diff --git a/(\S+)", text, re.M)]


def main() -> int:
    exp = ROOT / "results" / sys.argv[1]
    iid = sys.argv[2] if len(sys.argv) > 2 else None

    meta = {r["instance_id"]: r for r in load_swe_bench_lite()}

    for pf in sorted((exp / "patches").glob("*.txt")):
        name = pf.stem
        instance = re.sub(r"_(direct|planning|review)$", "", name)
        if iid and instance != iid:
            continue

        gold = meta.get(instance, {}).get("test_patch", "") or ""
        gold_files = sorted(collect_test_files(gold))
        patch_files = files_in_patch(pf.read_text(encoding="utf-8"))

        print(f"=== {name} ===")
        print(f"  gold test files ({len(gold_files)}):")
        for f in gold_files:
            print(f"    {f}")
        print(f"  model patch files ({len(patch_files)}):")
        for f in patch_files:
            mark = ""
            if f in gold_files:
                mark = "  <-- WOULD COLLIDE (not stripped!)"
            elif "/tests/" in f or f.startswith("test") or "/test_" in f:
                mark = "  (test file, not in gold patch)"
            print(f"    {f}{mark}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
