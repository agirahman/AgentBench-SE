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

    @pytest.mark.parametrize(
        "output",
        [
            # The bare phrase "no such file or directory" used to be a marker on
            # its own, so every filesystem probe was reported as a missing module.
            # Measured: 3 of 9 ordinary shell results were misclassified, and the
            # cost went UP once the sandbox could run tests, because an agent now
            # probes the filesystem far more and got told to install a package for
            # a directory that simply is not there.
            "ls: cannot access '/work/.git/objects': No such file or directory",
            "ls: cannot access '/work/results': No such file or directory",
            "cat: /work/.env: No such file or directory",
            "find: '/work/nope': No such file or directory",
            # A test file that does not exist is the agent's typo, not the env.
            "ERROR: file or directory not found: tests/test_utils.py",
            # git being absent is BY DESIGN, not a broken environment.
            "sh: 1: git: not found",
            "usage: pytest [options] [file_or_dir] [...]",
        ],
    )
    def test_filesystem_probes_are_not_env_failures(self, output):
        assert _looks_like_env_failure(output) is False, (
            f"an ordinary shell result was reported as a broken environment, so "
            f"run_tests would tell the agent to install a package: {output!r}"
        )

    @pytest.mark.parametrize(
        "output",
        [
            # A missing SCRIPT does mean the environment is at fault -- Python
            # itself says so, and it is distinguishable from `ls` by the prefix.
            "python: can't open file 'tests/runtests.py': [Errno 2] No such file or directory",
            "python3: can't open file '/work/setup.py': [Errno 2] No such file or directory",
        ],
    )
    def test_a_missing_script_is_still_an_env_failure(self, output):
        """The narrow rule must not overshoot and hide a real missing-script case.

        This is the half that keeps the fix honest: "stop matching the phrase" would
        also stop detecting a genuinely absent script, which IS an environment
        problem worth telling the agent about.
        """
        assert _looks_like_env_failure(output) is True


class TestRunTestsGuard:
    def test_repeat_command_is_refused(self, monkeypatch):
        """A third identical call must be refused instead of executed."""
        # local-unsafe: the repeat guard sits on the host-execution path, which
        # plain local now refuses outright (the H finding).
        monkeypatch.setenv("SANDBOX_MODE", "local-unsafe")
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

    def test_env_failure_offers_the_offline_install_path(self, monkeypatch):
        """A missing module must be ACTIONABLE, not a dead end (2026-10-08).

        This test used to assert "Do NOT call run_tests again". That was correct
        when the image had no dependencies and nothing could be installed -- and is
        now actively wrong: it told the agent to give up on an environment it can
        fix in one command. The image pre-installs the repos' declared deps and
        ships an offline wheelhouse, so the message must point at that.

        The install hint appears in docker mode only: --network=none plus
        `no-index = true` mean pip can reach the wheelhouse and nothing else, so it
        cannot become a route to a released (oracle) package.
        """
        monkeypatch.setenv("SANDBOX_MODE", "docker")

        class FakeProc:
            returncode = 1
            stdout = ""
            stderr = "ModuleNotFoundError: No module named 'django'"

        monkeypatch.setattr(tools.subprocess, "run", lambda *a, **k: FakeProc())

        out = run_tests("python -m pytest")
        assert "[tests unavailable]" in out
        # Still tells the agent this is not its patch's fault...
        assert "NOT a problem with your fix" in out
        # ...but no longer tells it to stop, and gives it the fix.
        assert "Do NOT call run_tests again" not in out, (
            "the agent is still told to give up on an environment it can repair"
        )
        assert "--no-index" in out and "wheelhouse" in out, (
            f"the environment failure must say HOW to install offline: {out}"
        )

    def test_env_failure_omits_the_install_hint_in_local_mode(self, monkeypatch):
        """The hint must not be given where pip is blocked and could fetch an oracle.

        In local mode `_check_command_security` refuses pip, so telling the agent to
        run it would send it into a refusal -- and if the guard ever slipped, the
        host's network would make it a real leak.

        Uses ``local-unsafe`` because plain ``local`` now refuses to execute at all
        (the H finding); this test is about the hint text on the host-execution
        path, which is the branch ``local-unsafe`` reaches.
        """
        monkeypatch.setenv("SANDBOX_MODE", "local-unsafe")

        class FakeProc:
            returncode = 1
            stdout = ""
            stderr = "ModuleNotFoundError: No module named 'django'"

        monkeypatch.setattr(tools.subprocess, "run", lambda *a, **k: FakeProc())

        out = run_tests("python -m pytest")
        assert "[tests unavailable]" in out
        assert "--no-index" not in out, (
            f"an offline-install hint was given in local mode, where pip is "
            f"blocked and the network is reachable: {out}"
        )

    def test_genuine_failure_is_reported_normally(self, monkeypatch):
        # local-unsafe, not local: plain local now refuses to execute at all
        # (the H finding), so this host-execution path is only reachable here.
        monkeypatch.setenv("SANDBOX_MODE", "local-unsafe")

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

    def test_uses_project_interpreter_not_bare_python(self, tmp_path, monkeypatch):
        """LOCAL mode only: the command must name the venv interpreter.

        A bare `python` resolves to the system interpreter, where the venv's
        packages are absent. This is a property of the HOST shell, so it is pinned
        in local mode explicitly rather than depending on the ambient
        SANDBOX_MODE — otherwise the test asserts the wrong thing whenever the
        suite is run with SANDBOX_MODE=docker.
        """
        monkeypatch.setenv("SANDBOX_MODE", "local")
        (tmp_path / "pytest.ini").write_text("", encoding="utf-8")
        cmd = tools._guess_test_command(tmp_path)
        assert sys.executable in cmd

    def test_docker_mode_uses_bare_posix_python(self, tmp_path, monkeypatch):
        """DOCKER mode: the host's venv path is meaningless inside the container.

        `sys.executable` is a Windows path like ``D:\\...\\.venv\\Scripts\\python.exe``
        which does not exist in the Linux container (measured: exit 127). The
        command must therefore call the plain ``python`` on the container's PATH —
        the exact opposite of the local-mode rule above.
        """
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        (tmp_path / "pytest.ini").write_text("", encoding="utf-8")
        cmd = tools._guess_test_command(tmp_path)
        assert sys.executable not in cmd, (
            "the host interpreter path must not leak into the container command"
        )
        assert cmd.startswith("python "), f"expected bare posix python; got: {cmd}"
        assert "\\" not in cmd, f"a Windows path separator leaked in: {cmd}"


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
