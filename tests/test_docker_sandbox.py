"""Tests for Docker sandbox isolation, path translation, and preflight guards."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agents.tools as tools_mod
from agents.tools import (
    _guess_test_command,
    _translate_paths_to_container,
    get_sandbox_mode,
    run_tests,
    set_repo_root,
)
from providers.system_prompts import (
    TOOL_SYSTEM_PROMPT,
    get_tool_system_prompt,
)
import tools.verify_docker_sandbox as vrf_docker


class TestSandboxModeAndConfig:
    def test_default_mode_is_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        assert get_sandbox_mode() == "local"

    def test_mode_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        assert get_sandbox_mode() == "docker"


class TestPathTranslation:
    def test_translate_windows_backslash_inside_repo(self) -> None:
        root = Path(r"D:\datasets\repos\django\django\08a4ee06")
        cmd = r"python D:\datasets\repos\django\django\08a4ee06\tests\test_widget.py"
        translated = _translate_paths_to_container(cmd, root)
        assert translated == "python /work/tests/test_widget.py"

    def test_translate_forward_slash_inside_repo(self) -> None:
        root = Path(r"D:\datasets\repos\django\django\08a4ee06")
        cmd = 'python "D:/datasets/repos/django/django/08a4ee06/tests/test_widget.py"'
        translated = _translate_paths_to_container(cmd, root)
        assert translated == 'python "/work/tests/test_widget.py"'

    def test_translate_exact_root(self) -> None:
        root = Path(r"D:\datasets\repos\django\django\08a4ee06")
        cmd = r"cd D:\datasets\repos\django\django\08a4ee06 && pytest"
        translated = _translate_paths_to_container(cmd, root)
        assert translated == "cd /work && pytest"

    def test_preserve_paths_outside_repo(self) -> None:
        root = Path(r"D:\datasets\repos\django\django\08a4ee06")
        cmd = r"cat D:\datasets\repos\django\other_repo\file.txt"
        translated = _translate_paths_to_container(cmd, root)
        assert r"D:\datasets\repos\django\other_repo\file.txt" in translated
        assert "/work" not in translated

    def test_preserve_relative_posix_paths(self) -> None:
        root = Path(r"D:\datasets\repos\django\django\08a4ee06")
        cmd = "python -m pytest tests/test_forms.py"
        translated = _translate_paths_to_container(cmd, root)
        assert translated == cmd


class TestGuessTestCommand:
    def test_docker_mode_uses_posix_python(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "runtests.py").write_text("# runner", encoding="utf-8")

        cmd = _guess_test_command(tmp_path)
        assert cmd == "cd tests && python runtests.py"
        assert "\\" not in cmd

    def test_docker_mode_pytest(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        (tmp_path / "pytest.ini").write_text("[pytest]", encoding="utf-8")

        cmd = _guess_test_command(tmp_path)
        assert cmd == "python -m pytest -x -q"

    def test_local_mode_preserves_interpreter(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "local")
        (tmp_path / "pytest.ini").write_text("[pytest]", encoding="utf-8")

        cmd = _guess_test_command(tmp_path)
        assert "-m pytest" in cmd


class TestDockerExecutionInRunTests:
    def test_docker_invokes_correct_args(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        set_repo_root(tmp_path)

        captured_args = []

        def mock_subprocess_run(args, **kwargs):
            captured_args.extend(args)
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = "1 passed in 0.05s"
            mock_res.stderr = ""
            return mock_res

        monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

        res = run_tests("pytest tests/test_a.py")
        assert "1 passed" in res
        assert captured_args[0] == "docker"
        assert captured_args[1] == "run"
        assert "--network=none" in captured_args
        assert "--memory=2g" in captured_args
        assert "--cpus=2" in captured_args
        assert "--tmpfs" in captured_args
        assert "/work/.git" in captured_args
        assert "-v" in captured_args
        assert f"{tmp_path.resolve()}:/work" in captured_args
        assert "agentbench-sandbox:latest" in captured_args

    def test_daemon_unreachable_returns_clear_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        set_repo_root(tmp_path)

        def mock_subprocess_run(args, **kwargs):
            mock_res = MagicMock()
            mock_res.returncode = 1
            mock_res.stdout = ""
            mock_res.stderr = "error during connect: open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified."
            return mock_res

        monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

        res = run_tests("pytest")
        assert "[error] Docker daemon is not running or unreachable" in res

    def test_image_missing_returns_clear_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        set_repo_root(tmp_path)

        def mock_subprocess_run(args, **kwargs):
            mock_res = MagicMock()
            mock_res.returncode = 1
            mock_res.stdout = ""
            mock_res.stderr = "Unable to find image 'agentbench-sandbox:latest' locally\npull access denied"
            return mock_res

        monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

        res = run_tests("pytest")
        assert "[error] Docker image 'agentbench-sandbox:latest' not found locally" in res

    def test_git_masking_toggle(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        monkeypatch.setenv("DOCKER_MASK_GIT", "0")
        # Reload or re-evaluate module level constant
        monkeypatch.setattr(tools_mod, "_DOCKER_MASK_GIT", False)
        set_repo_root(tmp_path)

        captured_args = []

        def mock_subprocess_run(args, **kwargs):
            captured_args.extend(args)
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = "ok"
            mock_res.stderr = ""
            return mock_res

        monkeypatch.setattr(subprocess, "run", mock_subprocess_run)
        run_tests("echo 1")
        assert "/work/.git" not in captured_args


class TestSystemPromptsDocker:
    def test_prompt_includes_posix_hint_when_docker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        prompt = get_tool_system_prompt()
        assert "The execution sandbox is POSIX (Linux): use grep/ls/cat, not findstr/dir/type." in prompt

    def test_prompt_omits_posix_hint_when_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "local")
        prompt = get_tool_system_prompt()
        assert "POSIX (Linux)" not in prompt

    def test_prompt_tells_agent_git_is_unavailable_in_docker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Git is deliberately absent from the image AND .git is masked.

        Without this line the agent burns turns on `git log` / `git show` that
        cannot work, so the prompt must redirect it to the host-side git_diff tool.
        """
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        prompt = get_tool_system_prompt()
        assert "Git is not installed in the sandbox" in prompt
        assert "git_diff" in prompt

    def test_prompt_does_not_mention_git_absence_in_local_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """In local mode git IS available (host shell), so the hint must not appear."""
        monkeypatch.setenv("SANDBOX_MODE", "local")
        prompt = get_tool_system_prompt()
        assert "Git is not installed in the sandbox" not in prompt


class TestVerifyDockerSandboxTool:
    def test_verify_daemon_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def mock_run(args, **kwargs):
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = "Server: Docker Desktop"
            mock_res.stderr = ""
            return mock_res

        monkeypatch.setattr(subprocess, "run", mock_run)
        ok, msg = vrf_docker.check_daemon()
        assert ok is True
        assert "running" in msg

    def test_verify_daemon_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def mock_run(args, **kwargs):
            mock_res = MagicMock()
            mock_res.returncode = 1
            mock_res.stdout = ""
            mock_res.stderr = "error during connect"
            return mock_res

        monkeypatch.setattr(subprocess, "run", mock_run)
        ok, msg = vrf_docker.check_daemon()
        assert ok is False
        assert "not ready" in msg

    def test_verify_image_missing_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def mock_run(args, **kwargs):
            mock_res = MagicMock()
            mock_res.returncode = 1
            mock_res.stdout = ""
            mock_res.stderr = "Error: No such image"
            return mock_res

        monkeypatch.setattr(subprocess, "run", mock_run)
        ok, msg = vrf_docker.check_image()
        assert ok is False
        assert "not found locally" in msg


def _docker_daemon_alive() -> bool:
    """True when the real Docker daemon answers. Used only to SKIP, never to pass."""
    try:
        proc = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=15)
        return proc.returncode == 0
    except Exception:
        return False


def _sandbox_image_present() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", "agentbench-sandbox:latest"],
            capture_output=True, text=True, timeout=15,
        )
        return proc.returncode == 0
    except Exception:
        return False


requires_docker = pytest.mark.skipif(
    not (_docker_daemon_alive() and _sandbox_image_present()),
    reason="requires a running Docker daemon with agentbench-sandbox:latest built",
)


@requires_docker
class TestRealDockerIntegration:
    """Integration tests against the REAL sandbox image.

    These are deliberately not mocked: a mock would keep passing while the image
    drifted (e.g. someone adds a target package back into the Dockerfile). A skip
    is reported as a skip, never as a pass.
    """

    IMAGE = "agentbench-sandbox:latest"

    def _run(self, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", "run", "--rm", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )

    def test_git_binary_is_absent_by_design(self) -> None:
        """git is NOT installed: the agent must use the host-side git_diff tool."""
        proc = self._run(self.IMAGE, "sh", "-c", "command -v git || echo GIT_ABSENT")
        assert "GIT_ABSENT" in proc.stdout, (
            "git was found in the sandbox image; the design assumes it is absent "
            f"(so `git log` cannot leak history). got: {proc.stdout!r}"
        )

    def test_target_oracle_packages_are_absent(self) -> None:
        """No target package may be importable: those versions are the oracle."""
        code = (
            "import importlib\n"
            "missing = []\n"
            "for p in ['django','sympy','matplotlib','sklearn','seaborn','requests','numpy']:\n"
            "    try:\n"
            "        importlib.import_module(p)\n"
            "    except ImportError:\n"
            "        missing.append(p)\n"
            "print('MISSING:' + ','.join(missing))\n"
        )
        proc = self._run(self.IMAGE, "python", "-c", code)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip().startswith("MISSING:")
        found = proc.stdout.strip()[len("MISSING:"):].split(",")
        assert sorted(found) == sorted(
            ["django", "sympy", "matplotlib", "sklearn", "seaborn", "requests", "numpy"]
        ), f"a target package IS importable (oracle leak): {proc.stdout!r}"

    def test_git_dir_is_masked_when_checkout_is_mounted(self, tmp_path: Path) -> None:
        """--tmpfs /work/.git must hide packed-refs and objects from the container."""
        git_dir = tmp_path / ".git"
        (git_dir / "objects").mkdir(parents=True)
        (git_dir / "packed-refs").write_text(
            "1111111111111111111111111111111111111111 refs/tags/v9.9.9\n", encoding="utf-8"
        )
        (tmp_path / "real_file.py").write_text("print('visible')\n", encoding="utf-8")

        proc = self._run(
            "-v", f"{tmp_path.resolve()}:/work", "--tmpfs", "/work/.git",
            "-w", "/work", self.IMAGE, "sh", "-c",
            "ls /work/.git 2>&1; echo '---'; ls /work/.git/packed-refs 2>&1; "
            "echo '---'; cat /work/real_file.py",
        )
        out = proc.stdout
        assert "packed-refs" not in out.replace("ls: cannot access '/work/.git/packed-refs'", ""), (
            f"packed-refs leaked into the container: {out!r}"
        )
        assert "No such file or directory" in out, f"expected masked .git, got: {out!r}"
        assert "visible" in out, "the working tree must stay readable while .git is masked"

    def test_network_is_blocked_under_network_none(self) -> None:
        proc = self._run(
            "--network=none", self.IMAGE, "python", "-c",
            "import urllib.request; urllib.request.urlopen('https://example.com', timeout=3)",
        )
        assert proc.returncode != 0, "outbound request SUCCEEDED under --network=none"
        assert "URLError" in proc.stderr or "Network is unreachable" in proc.stderr

    def test_host_filesystem_is_not_mounted_without_v(self) -> None:
        proc = self._run(self.IMAGE, "sh", "-c", "ls /host_mnt 2>&1; ls /mnt 2>&1")
        assert "No such file or directory" in proc.stdout or proc.stdout.strip().endswith("total 0"), (
            f"/host_mnt unexpectedly accessible: {proc.stdout!r}"
        )
