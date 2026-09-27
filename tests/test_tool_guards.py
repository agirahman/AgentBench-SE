"""Tests for the tool-loop guards added after EXP-20260927-004.

Two failure modes were measured in that run:

1. ``run_tests`` always failed in a raw SWE-bench checkout (dependencies live in
   the evaluation harness's conda env, not the clone). One planning step spent
   >33 minutes and 49 tool calls retrying it with 8 different spellings, because
   nothing told the agent the environment was simply absent.
2. ``write_file`` could silently truncate an existing file, and an agent did
   rewrite ``django/forms/widgets.py`` wholesale and then had to reset the repo.

These tests pin the guards that stop both.
"""

import sys

import pytest

from agents import tools
from agents.tools import (
    _looks_like_env_failure,
    reset_test_guard,
    run_tests,
    set_repo_root,
    write_file,
)


@pytest.fixture(autouse=True)
def bound_repo(tmp_path):
    """Bind the sandbox to a temp dir, the way a real run binds an instance.

    ``_safe_path`` refuses to operate unless a repo root is bound
    (``repo_root_resolved()``), so tests must use ``set_repo_root`` rather than
    patching ``_repo_root`` — otherwise every tool call returns the
    "no repository is bound" error.
    """
    set_repo_root(tmp_path)
    reset_test_guard()
    yield tmp_path
    set_repo_root(None)
    reset_test_guard()


class TestEnvFailureDetection:
    @pytest.mark.parametrize(
        "output",
        [
            "ModuleNotFoundError: No module named 'django'",
            "python: No module named pytest",
            "'pytest' is not recognized as an internal or external command",
            "ImportError: cannot import name 'x'",
            "bash: command not found: pytest",
        ],
    )
    def test_detects_missing_environment(self, output):
        assert _looks_like_env_failure(output) is True

    def test_real_test_failure_is_not_env_failure(self):
        # A genuine assertion failure must NOT be reported as a broken sandbox.
        output = (
            "FAILED tests/test_x.py::test_y - AssertionError: 1 != 2\n"
            "1 failed, 3 passed in 0.4s"
        )
        assert _looks_like_env_failure(output) is False

    def test_passing_tests_are_not_env_failure(self):
        assert _looks_like_env_failure("5 passed in 0.2s") is False


class TestRunTestsGuard:
    def test_repeat_command_is_refused(self, monkeypatch):
        """A third identical call must be refused instead of executed."""
        calls = {"n": 0}

        class FakeProc:
            returncode = 0
            stdout = "1 passed"
            stderr = ""

        def fake_run(*a, **k):
            calls["n"] += 1
            return FakeProc()

        monkeypatch.setattr(tools.subprocess, "run", fake_run)

        first = run_tests("fake-cmd")
        second = run_tests("fake-cmd")
        third = run_tests("fake-cmd")

        assert "[stop]" in third
        # The refusal must not have executed a third command.
        assert calls["n"] == 2
        assert "[stop]" not in first and "[stop]" not in second

    def test_env_failure_tells_agent_to_stop(self, monkeypatch):
        class FakeProc:
            returncode = 1
            stdout = ""
            stderr = "ModuleNotFoundError: No module named 'django'"

        monkeypatch.setattr(tools.subprocess, "run", lambda *a, **k: FakeProc())

        out = run_tests("python -m pytest")
        assert "[tests unavailable]" in out
        assert "Do NOT call run_tests again" in out
        # It must explain that this is not the agent's fault.
        assert "NOT a problem with your fix" in out

    def test_genuine_failure_is_reported_normally(self, monkeypatch):
        class FakeProc:
            returncode = 1
            stdout = "1 failed, 3 passed"
            stderr = ""

        monkeypatch.setattr(tools.subprocess, "run", lambda *a, **k: FakeProc())

        out = run_tests("pytest")
        assert "[exit code 1]" in out
        assert "1 failed, 3 passed" in out
        assert "[tests unavailable]" not in out

    def test_guard_reset_clears_history(self, monkeypatch):
        class FakeProc:
            returncode = 0
            stdout = "ok"
            stderr = ""

        monkeypatch.setattr(tools.subprocess, "run", lambda *a, **k: FakeProc())

        run_tests("cmd-a")
        run_tests("cmd-a")
        reset_test_guard()
        # After reset the same command is allowed twice again.
        assert "[stop]" not in run_tests("cmd-a")
        assert "[stop]" not in run_tests("cmd-a")

    def test_missing_repo_dir_is_an_error(self):
        set_repo_root("/nonexistent/repo/xyz")
        out = run_tests("pytest")
        assert "[error] repo dir not found" in out


class TestGuessTestCommand:
    def test_django_layout_uses_runtests(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "runtests.py").write_text("", encoding="utf-8")
        cmd = tools._guess_test_command(tmp_path)
        assert "runtests.py" in cmd
        assert "cd tests" in cmd

    def test_pytest_layout_uses_pytest(self, tmp_path):
        (tmp_path / "pytest.ini").write_text("", encoding="utf-8")
        cmd = tools._guess_test_command(tmp_path)
        assert "-m pytest" in cmd

    def test_uses_project_interpreter_not_bare_python(self, tmp_path):
        """A bare `python` resolves to the system interpreter, where the venv's
        packages are absent. The command must name the project interpreter."""
        (tmp_path / "pytest.ini").write_text("", encoding="utf-8")
        cmd = tools._guess_test_command(tmp_path)
        assert sys.executable in cmd


class TestWriteFileShrinkGuard:
    def test_refuses_drastic_shrink(self, bound_repo):
        target = bound_repo / "big.py"
        target.write_text("x = 1\n" * 2000, encoding="utf-8")  # ~12k chars
        before = target.read_text(encoding="utf-8")

        out = write_file("big.py", "x = 1\n")
        assert "[refused]" in out
        assert "truncated rewrite" in out
        # The file must be untouched.
        assert target.read_text(encoding="utf-8") == before

    def test_allows_normal_edit_of_small_file(self, bound_repo):
        target = bound_repo / "small.py"
        target.write_text("a = 1\n", encoding="utf-8")
        out = write_file("small.py", "a = 2\n")
        assert "[ok] overwrote" in out
        assert target.read_text(encoding="utf-8") == "a = 2\n"

    def test_allows_creating_new_file(self, bound_repo):
        out = write_file("new/mod.py", "print(1)\n")
        assert "[ok] created" in out
        assert (bound_repo / "new" / "mod.py").exists()

    def test_allows_similar_size_rewrite(self, bound_repo):
        """A legitimate rewrite of a large file must not be blocked."""
        target = bound_repo / "big.py"
        target.write_text("x = 1\n" * 2000, encoding="utf-8")
        # 80% of the original size — suspicious-free.
        new_content = "x = 2\n" * 1600
        out = write_file("big.py", new_content)
        assert "[ok] overwrote" in out

    def test_small_file_can_shrink_freely(self, bound_repo):
        """The guard only protects large files; shrinking a small one is fine."""
        target = bound_repo / "small.py"
        target.write_text("a = 1\n" * 100, encoding="utf-8")  # 600 chars
        out = write_file("small.py", "a = 1\n")
        assert "[ok] overwrote" in out


class TestPathNormalization:
    """Paths in shell-native shapes must be rewritten, not rejected.

    Measured on EXP-20260823-010: the model asked for
    ``/d/development/.../datasets/repos/django/django/<sha>/file.py`` (Git-Bash
    style) and the sandbox rejected it, so the agent retried the same shape and
    burned turns. A path that maps onto a real file under the repo root is now
    rewritten to repo-relative form.
    """

    def test_msys_path_mapping_to_real_file_is_rewritten(self, bound_repo):
        from agents.tools import _normalize_tool_path

        target = bound_repo / "requests" / "sessions.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n", encoding="utf-8")

        # Git-Bash style absolute path pointing at the file we just created.
        msys = "/" + str(target).replace("\\", "/").replace(":", "", 1)
        out = _normalize_tool_path(msys)
        assert out.replace("\\", "/") == "requests/sessions.py"

    def test_absolute_path_under_root_is_rewritten(self, bound_repo):
        from agents.tools import _normalize_tool_path

        target = bound_repo / "pkg" / "mod.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n", encoding="utf-8")

        out = _normalize_tool_path(str(target))
        assert out.replace("\\", "/") == "pkg/mod.py"

    def test_unmappable_path_is_returned_unchanged(self, bound_repo):
        """A path that matches no file is passed through, so the sandbox rejects
        it explicitly rather than silently reading somewhere else."""
        from agents.tools import _normalize_tool_path

        out = _normalize_tool_path("/d/nowhere/does-not-exist.py")
        assert out == "/d/nowhere/does-not-exist.py"

    def test_relative_path_is_untouched(self, bound_repo):
        from agents.tools import _normalize_tool_path

        assert _normalize_tool_path("requests/sessions.py") == "requests/sessions.py"

    def test_empty_path_is_untouched(self, bound_repo):
        from agents.tools import _normalize_tool_path

        assert _normalize_tool_path("") == ""
