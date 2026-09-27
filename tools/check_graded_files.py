"""Which files hold the GRADED tests? (corrected)

The SWE-bench Lite Django rows label tests the way Django's runner does:

    "test_override_file_upload_permissions (test_utils.tests.OverrideSettingsTests)"

That is `module.Class.method`, NOT pytest's `path::Class::method`. A first version
of this check compared a file path against those labels directly, so it could only
ever answer "not graded" — a false negative. This version maps a Django label to a
source file properly:

    test_utils.tests.OverrideSettingsTests  ->  tests/test_utils/tests.py

and then answers the question that matters: does a model patch touch a file whose
tests are actually graded (FAIL_TO_PASS / PASS_TO_PASS)?
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dataset_loader import load_swe_bench_lite  # noqa: E402


def as_list(v) -> list[str]:
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, list) else [v]
        except json.JSONDecodeError:
            return [v]
    return []


def module_of(label: str) -> str:
    """Extract the dotted module from a Django test label.

    'test_x (test_utils.tests.SomeClass)' -> 'test_utils.tests'
    'test_utils.tests.SomeClass.test_x'   -> 'test_utils.tests'

    Returns "" for entries that are not test labels at all. The dataset contains
    a few free-text PASS_TO_PASS entries (e.g. a docstring-like phrase), and
    treating those as module paths produced nonsense files such as
    'tests/An exception is setUp() .../__init__.py'.
    """
    m = re.search(r"\(([\w.]+)\)", label)
    dotted = m.group(1) if m else label.strip()
    # A real label is dotted identifiers only; anything else is free text.
    if not re.fullmatch(r"[\w.]+", dotted):
        return ""
    parts = dotted.split(".")
    if len(parts) < 2:
        return ""
    while parts and (parts[-1][:1].isupper() or parts[-1].startswith("test_")):
        parts.pop()
    return ".".join(parts)


def file_candidates(module: str) -> list[str]:
    """Django module path -> possible repo-relative source files.

    The Django test suite lives under tests/, and a module 'a.b' maps to
    'tests/a/b.py' or the package form 'tests/a/b/__init__.py'.
    """
    if not module:
        return []
    rel = module.replace(".", "/")
    return [f"tests/{rel}.py", f"tests/{rel}/__init__.py"]


def main() -> int:
    iid = sys.argv[1] if len(sys.argv) > 1 else "django__django-10914"
    row = next((r for r in load_swe_bench_lite() if r["instance_id"] == iid), None)
    if row is None:
        print(f"instance not found: {iid}")
        return 1

    f2p = as_list(row.get("FAIL_TO_PASS", []))
    p2p = as_list(row.get("PASS_TO_PASS", []))

    f2p_files, p2p_files = set(), set()
    for label in f2p:
        f2p_files.update(file_candidates(module_of(label)))
    for label in p2p:
        p2p_files.update(file_candidates(module_of(label)))

    print(f"instance: {iid}")
    print(f"  FAIL_TO_PASS labels: {len(f2p)}   PASS_TO_PASS labels: {len(p2p)}")
    print(f"  sample label: {f2p[0] if f2p else '(none)'}")
    print(f"  -> module: {module_of(f2p[0]) if f2p else '?'}")
    print()
    print(f"  files holding FAIL_TO_PASS ({len(f2p_files)}):")
    for f in sorted(f2p_files):
        print(f"    {f}")
    print()
    print(f"  files holding PASS_TO_PASS ({len(p2p_files)}):")
    for f in sorted(p2p_files):
        print(f"    {f}")
    print()

    gold_files = sorted(
        {m.group(1) for m in re.finditer(r"^diff --git a/(\S+)", row.get("test_patch", "") or "", re.M)}
    )
    print(f"  gold test_patch files ({len(gold_files)}):")
    for f in gold_files:
        print(f"    {f}")
    print()

    touched = [
        "tests/file_storage/tests.py",
        "tests/staticfiles_tests/test_storage.py",
    ]
    print("  model-touched test files (EXP-20260927-006):")
    for f in touched:
        print(f"    {f}: F2P={f in f2p_files} P2P={f in p2p_files} "
              f"-> {'GRADED (hazard)' if (f in f2p_files or f in p2p_files) else 'not graded'}")
    print()
    print("  consistency check (gold test_patch should hold the graded tests):")
    print(f"    F2P files == gold files? {sorted(f2p_files) == gold_files}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
