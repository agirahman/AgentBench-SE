"""Show the actual diff content of the core source file, per strategy.

Hunk headers alone only say *where* a change is, not *what* it is — two patches
can both show `@@ -32,7 +32,7 @@` and change different things. For django-11001
direct/planning RESOLVED while review FAILED, so the only way to tell whether the
review patch carried the same one-line fix (and something else broke it) or a
different fix is to read the diff body.

Also reports whether each model-touched test file is actually graded
(FAIL_TO_PASS / PASS_TO_PASS), since editing a graded test file would be a real
evaluation hazard rather than harmless noise.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path("D:/development/Skripsi2/AgantBech-SE")
sys.path.insert(0, str(ROOT / "src"))
exp = ROOT / "results" / "EXP-20260927-007"
out = Path(sys.argv[1])

from dataset_loader import load_swe_bench_lite  # noqa: E402

meta = {r["instance_id"]: r for r in load_swe_bench_lite()}

_buf = []
def emit(s=""): _buf.append(s)


def section(text: str, path: str) -> str:
    """Return the `diff --git a/<path>` section of a multi-file patch."""
    parts = re.split(r"(?m)^(?=diff --git )", text)
    for p in parts:
        if p.startswith(f"diff --git a/{path} "):
            return p
    return ""


def as_list(v):
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
    """Django test label -> dotted module.

    Two shapes appear in the dataset:
      'test_x (module.Class)'      -> 'module'   (parenthesised: last part is a Class)
      'module.Class.test_x'        -> 'module'   (dotted: last part is a method)

    Only capitalised trailing parts (the Class) may be stripped in the first
    shape. An earlier version also stripped any part starting with 'test_', which
    turned 'model_fields.test_filepathfield.FilePathFieldTests' into
    'model_fields' and pointed at the wrong file — module names like
    'test_filepathfield' are legitimate and must survive.
    """
    m = re.search(r"\(([\w.]+)\)", label)
    if m:
        dotted = m.group(1)
        parts = dotted.split(".")
        while len(parts) > 1 and parts[-1][:1].isupper():
            parts.pop()
        return ".".join(parts)
    dotted = label.strip()
    if not re.fullmatch(r"[\w.]+", dotted):
        return ""
    parts = dotted.split(".")
    if len(parts) < 2:
        return ""
    # dotted form: drop trailing method (test_*) then the Class
    if parts[-1].startswith("test_"):
        parts.pop()
    while len(parts) > 1 and parts[-1][:1].isupper():
        parts.pop()
    return ".".join(parts)


def graded_files(iid: str) -> set[str]:
    row = meta.get(iid, {})
    files = set()
    for key in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        for label in as_list(row.get(key, [])):
            mod = module_of(label)
            if mod:
                rel = mod.replace(".", "/")
                files.add(f"tests/{rel}.py")
                files.add(f"tests/{rel}/__init__.py")
    return files


CASES = [
    ("django__django-11001", "django/db/models/sql/compiler.py",
     {"direct": True, "planning": True, "review": False}),
    ("django__django-10924", "django/db/models/fields/__init__.py",
     {"direct": False, "planning": True, "review": False}),
]

for iid, core, resolved in CASES:
    emit(f"########## {iid}  (core file: {core}) ##########")
    emit()
    for s in ("direct", "planning", "review"):
        pf = exp / "patches" / f"{iid}_{s}.txt"
        text = pf.read_text(encoding="utf-8")
        verdict = "RESOLVED" if resolved[s] else "FAILED"
        emit(f"===== {s}  [{verdict}] =====")
        sec = section(text, core)
        if sec:
            # strip the file header lines for readability, keep hunks
            body = [ln for ln in sec.splitlines()
                    if ln.startswith(("@@", "+", "-")) and not ln.startswith(("+++", "---"))]
            for ln in body:
                emit(f"  {ln[:150]}")
        else:
            emit(f"  (no changes to {core})")
        emit()

    # graded-file hazard check
    gfiles = graded_files(iid)
    emit(f"  graded test files for {iid} ({len(gfiles)}):")
    for f in sorted(gfiles):
        emit(f"    {f}")
    emit()
    for s in ("direct", "planning", "review"):
        pf = exp / "patches" / f"{iid}_{s}.txt"
        text = pf.read_text(encoding="utf-8")
        touched = re.findall(r"^diff --git a/(\S+)", text, re.M)
        tests = [f for f in touched if "/tests/" in f or f.startswith("tests/")]
        if tests:
            emit(f"  {s}: model touched test files:")
            for f in tests:
                graded = "GRADED (hazard!)" if f in gfiles else "not graded"
                emit(f"      {f}  -> {graded}")
    emit()

out.write_text("\n".join(_buf) + "\n", encoding="utf-8")
print("ok")
