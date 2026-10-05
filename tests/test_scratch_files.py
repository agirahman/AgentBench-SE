"""Agent scratch files must never reach a submitted patch.

Agents write throwaway check scripts (``_tmp_check.py``) inside the repo they are
patching and sometimes forget to delete them, so ``git diff`` captured them.
Measured across three experiments, always on the ``direct`` arm:

    EXP-20260930-030  django__django-11019  _check_merge.py
    EXP-20260930-215  django__django-11019  _verify_merge.py
    EXP-20261001-765  django__django-11001  _tmp_check.py  (shipped as an empty file)

The filter is deterministic and applies to all three strategies equally, so it
does not advantage one arm over another.
"""
from __future__ import annotations

import pytest

from agents.tools import _is_scratch_file, drop_scratch_files


@pytest.mark.parametrize(
    "path",
    [
        "_tmp_check.py",
        "_verify_merge.py",
        "_check_merge.py",
        "_run_media_tests.py",
        "_out.txt",
        "_out.log",
        "tmp_probe.py",
        "scratch_work.py",
        "nested/dir/_tmp_check.py",
    ],
)
def test_scratch_files_are_recognised(path):
    assert _is_scratch_file(path) is True


@pytest.mark.parametrize(
    "path",
    [
        # The regression that matters: an earlier `^_.*\.py$` rule matched this
        # basename and would have deleted a real Django module from 2 of 15 pilot
        # patches. Dunders must never be treated as scratch.
        "django/db/models/fields/__init__.py",
        "django/__init__.py",
        "__main__.py",
        "django/utils/checks.py",
        "django/conf/global_settings.py",
        "django/db/models/sql/compiler.py",
        "django/forms/widgets.py",
        "tests/test_utils/tests.py",
        "docs/ref/settings.txt",
    ],
)
def test_real_project_files_are_never_scratch(path):
    assert _is_scratch_file(path) is False


def test_drop_removes_only_the_scratch_section():
    patch = (
        "diff --git a/_tmp_check.py b/_tmp_check.py\n"
        "new file mode 100644\n"
        "index 0000000..e69de29\n"
        "diff --git a/django/db/models/sql/compiler.py b/django/db/models/sql/compiler.py\n"
        "index 7649c39..05031c7 100644\n"
        "--- a/django/db/models/sql/compiler.py\n"
        "+++ b/django/db/models/sql/compiler.py\n"
        "@@ -32,7 +32,7 @@ class SQLCompiler:\n"
        "-        self.ordering_parts = re.compile(r'(.*)\\s(ASC|DESC)(.*)')\n"
        "+        self.ordering_parts = re.compile(r'^(.*?)\\s(ASC|DESC)(.*)', re.M)\n"
    )
    filtered, dropped = drop_scratch_files(patch)
    assert dropped == ["_tmp_check.py"]
    assert "_tmp_check.py" not in filtered
    assert "django/db/models/sql/compiler.py" in filtered
    # The real fix survives byte for byte.
    assert "+        self.ordering_parts = re.compile(r'^(.*?)\\s(ASC|DESC)(.*)', re.M)\n" in filtered


def test_drop_leaves_a_clean_patch_untouched():
    patch = (
        "diff --git a/django/forms/widgets.py b/django/forms/widgets.py\n"
        "index aaa..bbb 100644\n"
        "--- a/django/forms/widgets.py\n"
        "+++ b/django/forms/widgets.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    filtered, dropped = drop_scratch_files(patch)
    assert dropped == []
    assert filtered == patch


def test_drop_preserves_a_real_init_edit():
    """The exact false positive that was caught: editing django fields/__init__.py."""
    patch = (
        "diff --git a/django/db/models/fields/__init__.py b/django/db/models/fields/__init__.py\n"
        "index aaa..bbb 100644\n"
        "--- a/django/db/models/fields/__init__.py\n"
        "+++ b/django/db/models/fields/__init__.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-y = 1\n"
        "+y = 2\n"
    )
    filtered, dropped = drop_scratch_files(patch)
    assert dropped == []
    assert filtered == patch


def test_patch_of_only_scratch_becomes_empty():
    only = "diff --git a/_tmp_check.py b/_tmp_check.py\nnew file mode 100644\nindex 0000000..e69de29\n"
    filtered, dropped = drop_scratch_files(only)
    assert dropped == ["_tmp_check.py"]
    assert filtered == ""


def test_drop_is_a_noop_on_empty_input():
    assert drop_scratch_files("") == ("", [])
