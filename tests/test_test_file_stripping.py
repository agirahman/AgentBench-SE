"""Tests for stripping test files out of a model patch.

Rationale (see swebench_adapter.strip_test_files): the SWE-bench harness resets
test files with `git checkout <base_commit> <path>`, which fails for paths that
do not exist at base_commit. A model patch that creates a test file the gold
patch also creates therefore breaks the harness's own test application, and the
eval script continues anyway (no `set -e`). Keeping test files out of the
submitted patch removes the hazard.
"""

from experiments.swebench_adapter import collect_test_files, strip_test_files


GOLD_TEST_PATCH = """diff --git a/tests/model_fields/test_filepathfield.py b/tests/model_fields/test_filepathfield.py
new file mode 100644
index 0000000000..abcd1234
--- /dev/null
+++ b/tests/model_fields/test_filepathfield.py
@@ -0,0 +1,5 @@
+from django.test import TestCase
+
+
+class Foo(TestCase):
+    pass
"""


MODEL_PATCH = """diff --git a/django/db/models/fields/__init__.py b/django/db/models/fields/__init__.py
index 1111111111..2222222222 100644
--- a/django/db/models/fields/__init__.py
+++ b/django/db/models/fields/__init__.py
@@ -10,6 +10,7 @@ class Field:
     def __init__(self):
         self.name = None
+        self.path = None
         self.verbose_name = None
         return
diff --git a/tests/model_fields/test_filepathfield.py b/tests/model_fields/test_filepathfield.py
new file mode 100644
index 0000000000..ffffffff
--- /dev/null
+++ b/tests/model_fields/test_filepathfield.py
@@ -0,0 +1,3 @@
+from django.test import TestCase
+
+# a model-authored test that would collide with the gold test file
"""


class TestCollectTestFiles:
    def test_extracts_paths_from_diff_git_headers(self):
        files = collect_test_files(GOLD_TEST_PATCH)
        assert files == {"tests/model_fields/test_filepathfield.py"}

    def test_extracts_multiple_files(self):
        files = collect_test_files(MODEL_PATCH)
        assert files == {
            "django/db/models/fields/__init__.py",
            "tests/model_fields/test_filepathfield.py",
        }

    def test_empty_patch_gives_empty_set(self):
        assert collect_test_files("") == set()

    def test_dev_null_is_not_a_path(self):
        # +++ /dev/null appears for deleted files and must not be a path.
        patch = (
            "diff --git a/old.py b/old.py\n"
            "deleted file mode 100644\n"
            "--- a/old.py\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n-x\n"
        )
        assert collect_test_files(patch) == {"old.py"}


class TestStripTestFiles:
    def test_removes_only_the_test_file(self):
        filtered, removed = strip_test_files(MODEL_PATCH, {"tests/model_fields/test_filepathfield.py"})
        assert removed == ["tests/model_fields/test_filepathfield.py"]
        assert "django/db/models/fields/__init__.py" in filtered
        assert "test_filepathfield.py" not in filtered
        # The surviving section must still be a well-formed diff.
        assert filtered.startswith("diff --git a/django/db/models/fields/__init__.py")
        assert "self.path = None" in filtered

    def test_keeps_source_changes_intact(self):
        filtered, removed = strip_test_files(MODEL_PATCH, {"tests/model_fields/test_filepathfield.py"})
        # Exactly one file section survives, and its hunk body is untouched.
        assert filtered.count("diff --git") == 1
        assert "@@ -10,6 +10,7 @@" in filtered

    def test_patch_of_only_tests_becomes_empty(self):
        filtered, removed = strip_test_files(GOLD_TEST_PATCH, {"tests/model_fields/test_filepathfield.py"})
        assert removed == ["tests/model_fields/test_filepathfield.py"]
        assert filtered.strip() == ""

    def test_unrelated_test_files_are_not_removed(self):
        filtered, removed = strip_test_files(MODEL_PATCH, {"tests/other/test_thing.py"})
        assert removed == []
        assert filtered == MODEL_PATCH

    def test_no_test_files_is_a_noop(self):
        filtered, removed = strip_test_files(MODEL_PATCH, set())
        assert removed == []
        assert filtered == MODEL_PATCH

    def test_empty_patch_is_a_noop(self):
        filtered, removed = strip_test_files("", {"a.py"})
        assert removed == []
        assert filtered == ""

    def test_real_world_case_django_10924(self):
        """The measured collision: model created the same test file as gold."""
        filtered, removed = strip_test_files(MODEL_PATCH, collect_test_files(GOLD_TEST_PATCH))
        assert removed == ["tests/model_fields/test_filepathfield.py"]
        assert "test_filepathfield.py" not in filtered
        assert "django/db/models/fields/__init__.py" in filtered
