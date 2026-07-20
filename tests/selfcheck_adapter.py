"""Self-check: extract_diff menangani semua edge case model response."""
import sys
sys.path.insert(0, "src")

from experiments.swebench_adapter import extract_diff, _is_valid_patch_syntax, _check_patch_syntax

# Case 1: raw patch with hunk count mismatch → auto-fix succeeds → VALID
case1 = r"""diff --git a/django/conf/global_settings.py b/django/conf/global_settings.py
--- a/django/conf/global_settings.py
+++ b/django/conf/global_settings.py
@@ -245,6 +245,7 @@
 FILE_UPLOAD_MAX_MEMORY_SIZE = 2621440  # i.e. 2.5 MB
 FILE_UPLOAD_HANDLERS = (
     'django.core.files.uploadhandler.MemoryFileUploadHandler',
+    FILE_UPLOAD_PERMISSIONS = 0o644
 )"""
r1 = extract_diff(case1)
assert r1.status == "NORMALIZE", f"case1: expected NORMALIZE (hunk header normalized), got {r1.status}"
assert r1.patch.startswith("diff --git"), f"case1: expected diff after normalization, got {r1.patch[:80]!r}"

# Case 2: markdown-wrapped JSON (valid patch)
case2 = '```json\n{"patch": "diff --git a/foo.py b/foo.py\\n--- a/foo.py\\n+++ b/foo.py\\n@@ -1,4 +1,4 @@\\n x = 1\\n x = 2\\n-x = 3\\n+x = 3_new\\n x = 4"}\n```'
r2 = extract_diff(case2)
assert r2.patch.startswith("diff --git"), f"case2: expected diff, got {r2.patch[:80]!r}"
assert r2.status == "VALID", f"case2: expected VALID, got {r2.status}"

# Case 3: pure JSON (tanpa markdown wrapper)
case3 = '{"patch": "diff --git a/foo.py b/foo.py\\n--- a/foo.py\\n+++ b/foo.py\\n@@ -1,4 +1,4 @@\\n a = 1\\n a = 2\\n-a = 3\\n+a = 3_new\\n a = 4"}'
r3 = extract_diff(case3)
assert r3.patch.startswith("diff --git"), f"case3: expected diff, got {r3.patch[:80]!r}"
assert r3.status == "VALID", f"case3: expected VALID, got {r3.status}"

# Case 4: bare diff (no JSON, no markdown)
case4 = """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,4 +1,4 @@
 x = 1
 x = 2
 -x = 3
 +x = 3_new
 x = 4"""
r4 = extract_diff(case4)
assert r4.patch.startswith("diff --git"), f"case4: expected diff, got {r4.patch[:80]!r}"
assert r4.status == "NORMALIZE", f"case4: expected NORMALIZE, got {r4.status}"

# Case 5: empty response → EMPTY
r5 = extract_diff("")
assert r5.patch == "", f"case5: expected empty patch"
assert r5.status == "EMPTY", f"case5: expected EMPTY, got {r5.status}"

# Case 6: _is_valid_patch_syntax catches truncated hunk
assert _is_valid_patch_syntax(case1) is False

# Case 7: _is_valid_patch_syntax accepts valid diff
assert _is_valid_patch_syntax(r4.patch) is True

# Case 8: finish_reason='length' → TRUNCATED (bypass parsing)
r8 = extract_diff("some half-baked response", finish_reason="length")
assert r8.status == "TRUNCATED", f"case8: expected TRUNCATED, got {r8.status}"
assert r8.patch == "", f"case8: expected empty patch"

# Case 9: malformed hunk header literal (@@ -N,M +P,Q @@)
case9 = "diff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n@@ -N,M +P,Q @@\n"
failure = _check_patch_syntax(case9)
assert failure == "MALFORMED_HEADER", f"case9: expected MALFORMED_HEADER, got {failure}"

# Case 10: PLACEHOLDER_ONLY
case10 = "diff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n@@ -1,2 +1,2 @@\n # ...\n+# ...\n"
failure10 = _check_patch_syntax(case10)
assert failure10 == "PLACEHOLDER_ONLY", f"case10: expected PLACEHOLDER_ONLY, got {failure10}"

print("ALL CHECKS PASSED")
for i, r in enumerate([r1, r2, r3, r4, r5, r8], 1):
    print(f"  case {i}: status={r.status} len={len(r.patch)}")
