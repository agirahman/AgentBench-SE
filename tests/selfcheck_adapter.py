"""Self-check: extract_diff menangani semua edge case model response."""
import sys
sys.path.insert(0, "src")

from experiments.swebench_adapter import extract_diff, _is_valid_patch_syntax

# Case 1: raw truncated patch (model output jelek, hunk count salah)
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
assert r1 == "", f"case1: expected empty (truncated hunk), got {r1!r}"

# Case 2: markdown-wrapped JSON (valid patch)
# Header @@ -1,4 +1,4 @@ means 4 lines orig, 4 lines new
# Body: 2 context + 1 removed + 1 added + 1 context = 4 orig, 4 new = correct!
case2 = '```json\n{"patch": "diff --git a/foo.py b/foo.py\\n--- a/foo.py\\n+++ b/foo.py\\n@@ -1,4 +1,4 @@\\n x = 1\\n x = 2\\n-x = 3\\n+x = 3_new\\n x = 4"}\n```'
r2 = extract_diff(case2)
assert r2.startswith("diff --git"), f"case2: expected diff, got {r2[:80]!r}"

# Case 3: pure JSON (tanpa markdown wrapper)
case3 = '{"patch": "diff --git a/foo.py b/foo.py\\n--- a/foo.py\\n+++ b/foo.py\\n@@ -1,4 +1,4 @@\\n a = 1\\n a = 2\\n-a = 3\\n+a = 3_new\\n a = 4"}'
r3 = extract_diff(case3)
assert r3.startswith("diff --git"), f"case3: expected diff, got {r3[:80]!r}"

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
assert r4.startswith("diff --git"), f"case4: expected diff, got {r4[:80]!r}"

# Case 5: empty response
assert extract_diff("") == ""

# Case 6: _is_valid_patch_syntax catches truncated hunk
assert _is_valid_patch_syntax(case1) is False

# Case 7: _is_valid_patch_syntax accepts valid diff
assert _is_valid_patch_syntax(r4) is True

print("ALL CHECKS PASSED")
for i, r in enumerate([r1, r2, r3, r4], 1):
    print(f"  case {i}: len={len(r)} valid={bool(r.strip())}")
