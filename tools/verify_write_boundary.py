"""Does the refined guard still refuse a REPO write, and allow scratch work?

Run against the real repo root, because the distinction is exactly
"inside the repo" vs "outside it" -- a synthetic path would not test that.

This is a verification script, not a test: it prints the verdicts so the boundary
can be inspected by eye.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents import tools as T

#: The active repo root must be set for the inside/outside comparison to mean
#: anything. Use a real checkout.
REPO = ROOT / "datasets" / "repos" / "django" / "django"
candidates = sorted(p for p in REPO.iterdir() if p.is_dir()) if REPO.is_dir() else []
if not candidates:
    print("no checkout found; cannot test the boundary")
    raise SystemExit(1)
T.set_repo_root(candidates[0])
print(f"active repo root: {candidates[0]}")
print()

#: (command, should_be_refused)
CASES = [
    # --- must be REFUSED: writes into the repository ---
    ("python -c \"open('django/conf/global_settings.py','w').write('x')\"", True),
    ("python -c \"open('django/conf/global_settings.py','a').write('x')\"", True),
    ("sed -i 's/a/b/' django/forms/widgets.py", True),
    ("git checkout -- .", True),
    ("git apply evil.patch", True),
    ("git reset --hard", True),
    ("echo 'x = 1' > django/conf/global_settings.py", True),
    ("echo x >> django/forms/widgets.py", True),
    ("rm -rf tests", True),
    ("python -c \"import shutil; shutil.rmtree('tests')\"", True),
    ("python -c \"import os; os.remove('django/forms/widgets.py')\"", True),

    # --- must be ALLOWED: scratch work outside the repo ---
    ("cd /tmp && cat > t.py <<'EOF'\nimport django\nEOF", False),
    ("cd /tmp && python -c \"open('probe.py','w').write('x')\"", False),
    ("python -c \"open('/tmp/probe.py','w').write('x')\"", False),
    ("echo test > /tmp/out.txt", False),
    ("cd $TMPDIR && sed -i 's/a/b/' probe.py", False),

    # --- must be ALLOWED: read-only probes ---
    ("python -m pytest tests/file_storage/tests.py -q 2>&1 | tail -20", False),
    ("python -c \"import ast; ast.parse(open('x.py').read())\"", False),
    ("python -c \"print('OLD ->', x)\"", False),
    ("git diff --stat", False),
    ("git rev-parse HEAD", False),
    ("ls -la", False),
]

bad = 0
print(f"  {'refused?':<9} {'expected':<9} {'ok':<4} command")
print(f"  {'-' * 96}")
for command, should_refuse in CASES:
    matched = T._command_looks_like_a_write(command)
    refused = bool(matched)
    ok = refused == should_refuse
    if not ok:
        bad += 1
    label = "yes" if refused else "no"
    exp = "yes" if should_refuse else "no"
    flag = "OK" if ok else "WRONG"
    print(f"  {label:<9} {exp:<9} {flag:<4} {command[:74]!r}")
    if not ok:
        print(f"      matched pattern: {matched!r}")

T.set_repo_root(None)
print()
print("=" * 78)
if bad:
    print(f"  {bad} case(s) wrong -- the boundary is not where it should be")
else:
    print("  The boundary holds: repo writes refused, scratch work and probes allowed")
print("=" * 78)
raise SystemExit(1 if bad else 0)
