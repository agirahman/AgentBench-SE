"""Agent scratch files must never reach a submitted patch.

Agents write throwaway check scripts (``_tmp_check.py``) inside the repo they are
patching and sometimes forget to delete them, so ``git diff`` captured them.
Measured across three experiments, always on the ``direct`` arm:

    EXP-20260930-030  django__django-11019  _check_merge.py
    EXP-20260930-215  django__django-11019  _verify_merge.py
    EXP-20261001-765  django__django-11001  _tmp_check.py  (shipped as an empty file)

The filter is deterministic and applies to all three strategies equally, so it
does not advantage one arm over another.

Batch 1 of the paid sweep (EXP-20261010-062, route cbai/deepseek-v4.1-flash)
leaked a different naming scheme: the django__django-11019 review patch carried
18 files -- the real fix plus 17 throwaways (``_exp6.py``, ``_t1.txt``,
``_trace.py``, ``_dump.py``, ``_dis.py``, ``_hdr.py``, ``_base.txt``, ...).
Those short prefixes are generic, so the new patterns allow only a digit suffix
and match ONLY at the repository root -- both restrictions measured against all
53 checkouts of the study's five repos (0 false positives).
"""
from __future__ import annotations

import pytest

from agents.tools import _is_scratch_file, drop_scratch_files

#: The exact 17 scratch files from the EXP-20261010-062 leak (root of the
#: django checkout, alongside the real django/forms/widgets.py fix).
_BATCH1_LEAK_FILES = [
    "_base.txt",
    "_def.py",
    "_dis.py",
    "_dis.txt",
    "_dump.py",
    "_dump.txt",
    "_exp6.py",
    "_exp7.py",
    "_exp8.py",
    "_exp9.py",
    "_hdr.py",
    "_out6.txt",
    "_out7.txt",
    "_t1.txt",
    "_t2.txt",
    "_t3.txt",
    "_trace.py",
]


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
        # The long-prefix patterns still match at ANY depth: these prefixes are
        # unambiguous wherever they appear.
        "nested/dir/_tmp_check.py",
    ],
)
def test_scratch_files_are_recognised(path):
    assert _is_scratch_file(path) is True


@pytest.mark.parametrize("path", _BATCH1_LEAK_FILES)
def test_batch1_leak_files_are_recognised(path):
    """Every file from the EXP-20261010-062 leak must be recognised."""
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


@pytest.mark.parametrize(
    "path",
    [
        # Real modules whose basenames start with '_', found by scanning all 53
        # checkouts of the study's repos. The short-prefix patterns must not
        # touch them: `_dis[_A-Za-z0-9]*` (the obvious-looking rule) would have
        # deleted _discretization.py, and `_base[_A-Za-z0-9]*\.py` would have
        # deleted matplotlib's _base.py.
        "sklearn/preprocessing/_discretization.py",
        "lib/matplotlib/axes/_base.py",
        "django/utils/_os.py",
        "tests/migrations/test_migrations_private/_util.py",
        "sklearn/utils/_show_versions.py",
        "lib/matplotlib/_type1font.py",
    ],
)
def test_real_short_underscore_modules_are_never_scratch(path):
    assert _is_scratch_file(path) is False


@pytest.mark.parametrize(
    "path",
    [
        # The short, generic prefixes are ROOT-ONLY: a same-named file inside a
        # package is real code (or at minimum not something we can safely call
        # scratch). Measured: no real repo has a root-level file starting with
        # '_', and every nested one is load-bearing.
        "pkg/_dis.py",
        "pkg/_exp6.py",
        "pkg/_dump.txt",
        "django/_base.txt",
        "some/deep/dir/_t1.txt",
    ],
)
def test_short_prefixes_do_not_match_nested_files(path):
    assert _is_scratch_file(path) is False


# ---------------------------------------------------------------------------
# ROOT-LEVEL RESTRICTION PINS (the 6 mutations that survived the audit)
# ---------------------------------------------------------------------------
# The audit ran 23 mutations; 6 real gaps passed with a green suite. Each case
# below is the probe that distinguishes the real restriction from the widened
# mutant, at the REPO ROOT (where the root-only rule does not help -- only the
# suffix/extension restriction does).

@pytest.mark.parametrize(
    "path",
    [
        # M5: `_base` widened to allow .py. Root _base.py is a plausible real
        # module name (matplotlib's axes/_base.py is exactly that basename), so
        # _base may only ever match .txt/.log.
        "_base.py",
        # M6: `_dis` widened to `\w*`. Root _discretization.py and _dis_foo.py
        # must stay: the observed scratch name was exactly `_dis.py`/`_dis.txt`.
        "_discretization.py",
        "_dis_foo.py",
        # M7: `_t` widened to `\d*`. `_t.txt` (no digits) is not the observed
        # scheme -- `_t1.txt` is -- so it must not match.
        "_t.txt",
        # M19: `_exp` widened to `[_A-Za-z0-9]*`. `_exp_foo.py` must stay.
        "_exp_foo.py",
        # M20: `_t` widened to `[A-Za-z0-9]*`. `_test.txt` must stay -- this is
        # the one that would matter most, a real test artifact.
        "_test.txt",
        # Same class, other prefixes: a bare word suffix is never the scheme.
        "_dump_all.py",
        "_trace_utils.py",
        "_hdr_config.py",
        "_def_helpers.py",
        # _exp allows the bare form (`_exp.py`) per the brief's `\d*`, but not
        # arbitrary words; keep the digit-only neighbour pinned too.
        "_outx.txt",
    ],
)
def test_root_level_widened_suffixes_are_never_scratch(path):
    """The suffix restriction, measured at the root where it is the only guard.

    These are the exact probes that killed the six audit mutations. Without
    them, widening any suffix (e.g. `_dis` -> `\\w*`) left the suite green while
    the filter started deleting real root-level files.
    """
    assert _is_scratch_file(path) is False


def test_drop_keeps_a_nested_file_even_when_its_basename_looks_scratchy():
    """M18: dropping the root-guard must not let a NESTED file be removed.

    The root-only rule is invisible to a basename-only test: this one runs the
    real ``drop_scratch_files`` on a multi-file patch where a scratch-looking
    basename sits INSIDE a package. The nested chunk must survive.
    """
    patch = (
        "diff --git a/pkg/_dis.py b/pkg/_dis.py\n"
        "new file mode 100644\n"
        "index 0000000..e69de29\n"
        "diff --git a/django/forms/widgets.py b/django/forms/widgets.py\n"
        "index 8a1b2c3..9d4e5f6 100644\n"
        "--- a/django/forms/widgets.py\n"
        "+++ b/django/forms/widgets.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    filtered, dropped = drop_scratch_files(patch)
    assert dropped == [], (
        f"a nested file was dropped by basename alone: {dropped}"
    )
    assert filtered == patch, "the nested chunk must survive byte for byte"
    assert "pkg/_dis.py" in filtered


def test_drop_still_removes_the_root_level_twin():
    """The other half of M18: the SAME basename at the root IS scratch.

    Together with the test above this pins that the rule is about PATH, not
    name: same file name, different location, different verdict.
    """
    patch = (
        "diff --git a/_dis.py b/_dis.py\n"
        "new file mode 100644\n"
        "index 0000000..e69de29\n"
    )
    filtered, dropped = drop_scratch_files(patch)
    assert dropped == ["_dis.py"]
    assert filtered == ""


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


def test_drop_removes_batch1_scratch_and_keeps_the_real_fix():
    """The EXP-20261010-062 shape: 17 scratch files + 1 real fix.

    Only the scratch chunks may be dropped; the widgets.py hunk must survive
    byte for byte.
    """
    real = (
        "diff --git a/django/forms/widgets.py b/django/forms/widgets.py\n"
        "index 8a1b2c3..9d4e5f6 100644\n"
        "--- a/django/forms/widgets.py\n"
        "+++ b/django/forms/widgets.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    scratch_chunks = "".join(
        f"diff --git a/{name} b/{name}\n"
        f"new file mode 100644\n"
        f"index 0000000..e69de29\n"
        for name in _BATCH1_LEAK_FILES
    )
    patch = scratch_chunks + real

    filtered, dropped = drop_scratch_files(patch)

    assert sorted(dropped) == sorted(_BATCH1_LEAK_FILES)
    assert len(dropped) == 17
    assert filtered == real, "the real fix must survive byte for byte"
    kept_files = [
        l[len("diff --git a/"):].split(" b/")[0]
        for l in filtered.splitlines()
        if l.startswith("diff --git")
    ]
    assert kept_files == ["django/forms/widgets.py"]


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


def test_patch_of_only_batch1_scratch_becomes_empty():
    """A patch made entirely of the new naming scheme must vanish completely."""
    only = "".join(
        f"diff --git a/{name} b/{name}\n"
        f"new file mode 100644\n"
        f"index 0000000..e69de29\n"
        for name in _BATCH1_LEAK_FILES
    )
    filtered, dropped = drop_scratch_files(only)
    assert sorted(dropped) == sorted(_BATCH1_LEAK_FILES)
    assert filtered == ""


def test_drop_is_a_noop_on_empty_input():
    assert drop_scratch_files("") == ("", [])
