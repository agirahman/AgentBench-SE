"""Tests for Docker sandbox isolation, path translation, and preflight guards."""
from __future__ import annotations

import importlib
import re
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
    READONLY_GIT_DIFF_TOOL_SYSTEM_PROMPT,
    READONLY_TOOL_SYSTEM_PROMPT,
    TOOL_SYSTEM_PROMPT,
    get_readonly_system_prompt,
    get_tool_system_prompt,
)
import tools.verify_docker_sandbox as vrf_docker


class TestSandboxModeAndConfig:
    def test_default_mode_is_docker_fail_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An UNSET mode must mean ISOLATED, not unisolated.

        The default used to be 'local', so any entry point that forgot to set
        SANDBOX_MODE (the sweep and the runner both did) executed agent commands on
        the host with no isolation and no warning. Fail-closed is the correct
        direction: a missing daemon then produces a loud error instead of a silent
        unisolated run.
        """
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        assert get_sandbox_mode() == "docker"

    def test_mode_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        assert get_sandbox_mode() == "docker"

    def test_local_mode_still_available_but_explicit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Development convenience is kept, but it must now be requested."""
        monkeypatch.setenv("SANDBOX_MODE", "local")
        assert get_sandbox_mode() == "local"

    def test_prompt_default_matches_execution_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The prompt and the executor must agree when SANDBOX_MODE is unset.

        If they disagreed, a run would EXECUTE in one mode while being PROMPTED as
        if it were the other — telling the agent to use `dir`/`type` in a POSIX
        container, or that git works when it does not.
        """
        from providers.system_prompts import get_tool_system_prompt

        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        assert get_sandbox_mode() == "docker"
        assert "POSIX (Linux)" in get_tool_system_prompt()


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

    def test_prompt_forbids_hunting_the_masked_git_dir_in_docker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The masked .git must be declared OFF LIMITS, not merely mentioned.

        An agent that is only told "git is not installed" still tries to reach
        history another way: measured, one run spent ~4 hours on `umount
        /work/.git` and `find / -name refs`. The prompt must say the absence is by
        design and that searching for it wastes turns.
        """
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        prompt = get_tool_system_prompt()
        assert "intentionally unavailable/masked by design" in prompt
        assert "do NOT search for .git directories" in prompt
        assert "do not try to unmount them" in prompt
        assert "solve the issue from the source code alone" in prompt

    def test_prompt_does_not_mention_git_absence_in_local_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """In local mode git IS available (host shell), so the hint must not appear."""
        monkeypatch.setenv("SANDBOX_MODE", "local")
        prompt = get_tool_system_prompt()
        assert "Git is not installed in the sandbox" not in prompt
        assert "intentionally unavailable/masked by design" not in prompt


class TestReadonlySystemPromptsDocker:
    """planner / reviewer get the sandbox env hint too, or they hunt .git as well.

    Read-only roles were previously given a prompt with NO sandbox content at all,
    so the .git-hunting failure mode was open to them too. They must receive the
    environment half of the hint -- and must NOT receive the dependency half, which
    tells the reader to run tests and install wheels.
    """

    def test_readonly_prompt_carries_the_masked_git_hint_in_docker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        prompt = get_readonly_system_prompt()
        assert "intentionally unavailable/masked by design" in prompt
        assert "do NOT search for .git directories" in prompt
        assert "POSIX (Linux)" in prompt

    def test_readonly_prompt_stays_read_only_and_needs_no_test_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The env hint is inside the read-only framing, without the test/wheel text."""
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        prompt = get_readonly_system_prompt()
        assert "You may NOT modify files" in prompt
        assert "run_tests" not in prompt
        assert "pip install" not in prompt

    def test_readonly_prompt_omits_sandbox_hint_when_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "local")
        prompt = get_readonly_system_prompt()
        assert "intentionally unavailable/masked by design" not in prompt
        assert "POSIX (Linux)" not in prompt
        assert "You may NOT modify files" in prompt

    def test_module_level_readonly_constant_matches_the_helper(self) -> None:
        """The imported constant is built at import time; pin it against the helper.

        A test that only called the helper could pass while the shipped constant
        (what the agents actually receive) was stale or hand-edited.

        The constant is FROZEN at import, using whatever ``SANDBOX_MODE`` the
        process had then. Calling ``monkeypatch.setenv`` afterwards cannot change
        it, so the old version of this test compared a local-mode constant against a
        docker-mode helper and failed whenever the suite ran with
        ``SANDBOX_MODE=local``. The frozen mode is therefore read off the constant
        itself (the POSIX hint is present iff it was built in docker mode) and the
        helper is asked for THAT mode -- deterministic under any ambient env, and it
        still catches a stale or hand-edited constant.
        """
        built_in_docker = "POSIX (Linux)" in READONLY_TOOL_SYSTEM_PROMPT
        expected = get_readonly_system_prompt("docker" if built_in_docker else "local")
        assert READONLY_TOOL_SYSTEM_PROMPT == expected
        if built_in_docker:
            assert "intentionally unavailable/masked by design" in READONLY_TOOL_SYSTEM_PROMPT

    def test_readonly_prompt_names_git_diff_only_for_roles_that_have_it(self) -> None:
        """planner's tool set has no git_diff; reviewer's does. The hint must match.

        Telling a role to use a tool it does not have is the defect class this
        read-only framing exists to avoid (see system_prompts.py). The tool sets are
        read from AGENT_TOOLS rather than hard-coded, so this cannot drift from the
        real wiring.
        """
        from agents.tools import AGENT_TOOLS

        planner = get_readonly_system_prompt(
            "docker", has_git_diff="git_diff" in AGENT_TOOLS["planner"]
        )
        reviewer = get_readonly_system_prompt(
            "docker", has_git_diff="git_diff" in AGENT_TOOLS["reviewer"]
        )

        assert "git_diff" not in planner, "planner has no git_diff tool but is told to use it"
        assert "git_diff" in reviewer, "reviewer has git_diff but its prompt never points at it"
        # The masked-git rule must reach BOTH roles: both burn turns hunting .git.
        assert "intentionally unavailable/masked by design" in planner
        assert "intentionally unavailable/masked by design" in reviewer
        assert "POSIX (Linux)" in planner and "POSIX (Linux)" in reviewer
        assert "You may NOT modify files" in planner
        assert "You may NOT modify files" in reviewer

    def test_agent_wiring_selects_the_prompt_by_real_tool_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """End-to-end: the prompt each role ACTUALLY receives matches its tool set.

        The constants are pinned here so the assertion is independent of the
        shell's ``SANDBOX_MODE`` at import time; what is under test is which of them
        ``BaseAgent.act`` hands to the planner versus the reviewer.
        """
        planner = self._system_prompt_for("planner", monkeypatch)
        reviewer = self._system_prompt_for("reviewer", monkeypatch)

        assert "git_diff" not in planner
        assert "intentionally unavailable/masked by design" in planner
        assert "git_diff" in reviewer
        assert "intentionally unavailable/masked by design" in reviewer
        assert planner != reviewer

    @staticmethod
    def _system_prompt_for(role: str, monkeypatch: pytest.MonkeyPatch) -> str:
        """Drive one real agent's ``act`` and return the system prompt it used."""
        from agents.blackboard import Blackboard
        from agents.messages import AgentMessage
        from models.inference import InferenceResult
        from models.issue import Issue

        monkeypatch.setenv("SANDBOX_MODE", "docker")
        # Pin the frozen constants to docker-built ones, so the test does not depend
        # on the ambient mode at import time.
        monkeypatch.setattr(
            "agents.base.READONLY_TOOL_SYSTEM_PROMPT", get_readonly_system_prompt("docker")
        )
        monkeypatch.setattr(
            "agents.base.READONLY_GIT_DIFF_TOOL_SYSTEM_PROMPT",
            get_readonly_system_prompt("docker", has_git_diff=True),
        )
        monkeypatch.setattr("agents.base.TOOL_SYSTEM_PROMPT", get_tool_system_prompt("docker"))

        captured: list[str] = []

        class _CapturingProvider:
            def generate_with_tools(self, prompt, role="", tools=None, system_prompt="", **kwargs):
                captured.append(system_prompt)
                return InferenceResult(role=role, response="done")

            def generate(self, prompt, role=""):
                return InferenceResult(role=role, response="done")

        agent_cls = {
            "planner": "PlannerAgent",
            "reviewer": "ReviewerAgent",
        }[role]
        module = importlib.import_module(f"agents.{role}_agent")
        agent = getattr(module, agent_cls)(_CapturingProvider())
        agent.use_tools = True
        task = AgentMessage(sender="orchestrator", receiver=role, content="issue")
        context = Blackboard(issue=Issue("x", "psf/requests", "abc", "fix it"), plan="p", patch="d")
        agent.act(task, context)
        assert captured, f"the {role} agent made no tool-calling call"
        return captured[-1]


class TestSandboxCanRunTests:
    """The sandbox must let the agent TEST, which is the 2026-10-08 operator change.

    These are prompt-level assertions. The behavioural half lives in
    TestRealDockerIntegration (which needs the real image) and in
    test_network_block.py (which pins the pip rule per mode).
    """

    def test_prompt_tells_the_agent_dependencies_are_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The prompt used to tell the agent the deps were MISSING and to stop.

        That instruction was correct for the old image and is now backwards: it
        would suppress exactly the behaviour the operator asked for. Measured in the
        old image: 5 of 6 repos died on ModuleNotFoundError, so "believe it and
        stop" was the only option -- and in the new image django runs 127 tests.
        """
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        prompt = get_tool_system_prompt()
        assert "pre-installed" in prompt, (
            "the prompt does not say the dependencies are available, so the agent "
            "may skip testing"
        )
        # The old give-up instruction must be gone.
        assert "believe it and stop" not in prompt, (
            "the prompt still tells the agent to stop when a dependency is missing"
        )
        assert "lacks this repo's dependencies" not in prompt

    def test_prompt_gives_the_offline_install_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing module must come with the fix, not just a diagnosis.

        The container has no network, so a bare `pip install x` cannot work. The
        prompt must give the wheelhouse form or the agent will flail.
        """
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        prompt = get_tool_system_prompt()
        assert "--no-index" in prompt
        assert "/opt/wheelhouse" in prompt

    def test_prompt_omits_the_wheelhouse_hint_in_local_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """In local mode pip is blocked and the network is reachable.

        Advertising a wheelhouse there would be wrong twice: the path does not
        exist on the host, and pip is refused by the command guard.
        """
        monkeypatch.setenv("SANDBOX_MODE", "local")
        prompt = get_tool_system_prompt()
        assert "/opt/wheelhouse" not in prompt
        assert "--no-index" not in prompt

    def test_prompt_does_not_tell_the_agent_to_run_tests_outside_docker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The prompt must match what run_tests WILL DO (finding H).

        In docker mode run_tests runs the suite, so the agent is told to use it.
        Outside docker it REFUSES (this host has a WSL bash with internet, so no
        local execution can be contained). A prompt that still said "verify with
        run_tests" would send the agent into a refusal and read as a broken tool
        rather than a deliberate control -- and worse, it would teach the agent to
        keep trying a path that is closed on purpose.
        """
        monkeypatch.setenv("SANDBOX_MODE", "local")
        prompt = get_tool_system_prompt()
        assert "Verify with run_tests" not in prompt, (
            "the prompt tells the agent to run tests in a mode where run_tests "
            "refuses to execute"
        )
        assert "DISABLED" in prompt, (
            "the prompt does not explain why run_tests will refuse"
        )

        # And the docker prompt must still ask for the regression signal.
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        docker_prompt = get_tool_system_prompt()
        assert "Verify with run_tests" in docker_prompt, (
            "the docker prompt must still tell the agent to use the suite"
        )


class TestDockerfilePins:
    """The load-bearing pins must not be quietly dropped from the image.

    These read the Dockerfile rather than the built image so they run without a
    daemon. Each pin has a measured failure behind it (see the Dockerfile
    comments); a test that only checked "the build succeeds" would not notice a pin
    being removed, because the image still builds -- it just fails at TEST time,
    which is where the measurement was taken.
    """

    DOCKERFILE = ROOT / "docker" / "sandbox.Dockerfile"

    def _text(self) -> str:
        return self.DOCKERFILE.read_text(encoding="utf-8")

    def _preinstall_block(self) -> str:
        """ONLY the pre-install `pip install` RUN, not the whole file.

        Scoping matters and a first version got it wrong: `setuptools<60`,
        `pyparsing<3` and `Cython<3` also appear in the WHEELHOUSE download
        section, so a whole-file substring check was satisfied by that other block.
        Dropping a pin from the pre-install list therefore left the test GREEN --
        the same "a control satisfied by text elsewhere" trap this suite has hit
        before. Measured: M2/M3 stayed green until this was scoped.
        """
        text = self._text()
        start = text.index("RUN pip install --no-cache-dir")
        end = text.index("# --- wheelhouse", start)
        return text[start:end]

    def _wheelhouse_block(self) -> str:
        """ONLY the wheelhouse `pip download` RUN.

        Same scoping reason as _preinstall_block, in the other direction: the two
        blocks list similar packages, so a check on one must not be satisfiable by
        the other. This block is where a target package would most naturally be
        added, because it is where matplotlib USED to be.
        """
        text = self._text()
        start = text.index("RUN mkdir -p /opt/wheelhouse")
        end = text.index("# --- offline pip config", start)
        return text[start:end]

    @staticmethod
    def _package_specs(block: str) -> list[str]:
        """The package NAMES a pip command lists, ignoring comments and flags.

        Needed because the block's own explanatory comment names every target
        ("matplotlib is NOT here because ..."), so a naive substring scan flags the
        documentation instead of the code. Measured: a first version of
        test_no_target_package_in_the_wheelhouse_block reported all eight targets
        from the comment alone.

        A package line is one that is not a comment, not blank, and does not start
        with a dash or a continuation/redirect token.
        """
        names = []
        for raw in block.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("-", "&&", "|", ">", "RUN", "\\")):
                continue
            # Strip quotes, inline comments, and any version specifier.
            token = line.split("#", 1)[0].strip().strip("'\"")
            if not token or token.startswith("-"):
                continue
            m = re.match(r"^([A-Za-z][A-Za-z0-9_.\-]*)", token)
            if m:
                names.append(m.group(1).lower())
        return names

    def test_no_target_package_in_the_wheelhouse_block(self) -> None:
        """K-1 at the SOURCE level: the wheelhouse must not download a target.

        The image-level test (test_no_target_package_wheel_in_the_image) catches
        this too, but only when the image is rebuilt. This one fails the moment the
        Dockerfile is edited, which is when the mistake is actually made -- and it
        runs without a Docker daemon.

        matplotlib is the specific regression to guard: it was added here for
        seaborn's benefit and turned out to be a complete oracle for 4 instances.
        """
        specs = self._package_specs(self._wheelhouse_block())
        offenders = [n for n in specs
                     if any(n == t or n.startswith(t + "-") or n.startswith(t + "_")
                            for t in _TARGET_PACKAGES)]
        assert not offenders, (
            f"the Dockerfile downloads target package(s) into the wheelhouse: "
            f"{offenders}. A wheel is a ZIP, so its source -- including the released "
            f"version of the grading tests -- is readable with zipfile and no "
            f"install. Measured cost of this mistake: 10/50 instances readable, "
            f"4/50 a complete oracle. Found specs: {specs}"
        )

    def test_the_package_spec_parser_is_not_vacuous(self) -> None:
        """The parser above must actually find packages, or the test is decoration.

        A parser that returned [] for everything would make the wheelhouse check
        pass forever. This pins that it reads real specs from the real block.
        """
        specs = self._package_specs(self._wheelhouse_block())
        assert "setuptools" in specs, f"parser found nothing useful: {specs}"
        assert "wheel" in specs, f"parser missed a plain name: {specs}"
        assert len(specs) >= 5, f"parser found suspiciously few specs: {specs}"

    def test_the_build_refuses_a_target_wheelhouse(self) -> None:
        """The Dockerfile must GATE the build, not merely document the rule.

        A comment cannot stop a future edit. The gate runs the same target-name
        check at BUILD time and exits non-zero, so an image carrying an oracle
        cannot be produced even if the test suite is not run.

        The check requires an ACTIVE line, not a substring. A first version used
        `"sys.exit(...)" in block`, and a mutation that commented the gate out left
        the string present -- so the test stayed green while the build no longer
        failed. Measured. Comments are stripped first.
        """
        block = self._wheelhouse_block()
        active = [l for l in block.splitlines()
                  if not l.strip().startswith("#")]
        active_text = "\n".join(active)
        assert "sys.exit(1 if bad else 0)" in active_text, (
            "the wheelhouse build gate is missing, commented out, or no longer "
            "fails the build. Without it, adding a target package to the image is "
            "caught only by the test suite -- and measured, no test caught the "
            "gate's removal when it was merely commented out."
        )
        assert "FATAL" in active_text, (
            "the build gate no longer prints a FATAL diagnostic, so a failure would "
            "be hard to attribute"
        )

    def test_the_build_gate_actually_fires(self, tmp_path: Path) -> None:
        """The gate must FAIL when run against a wheelhouse WITH a target.

        test_the_build_refuses_a_target_wheelhouse only checks the TEXT -- a
        mutation that empties the target list, drops the glob, or no-ops the call
        leaves both strings intact. This runs the gate's LOGIC (the same glob +
        target-match against a fake wheelhouse dir containing a matplotlib wheel)
        and asserts it fires. Measured gap: 3 mutations (K1c/K1d/K1e) survived
        the text check.
        """
        import subprocess
        block = self._wheelhouse_block()
        # Strip comments TWICE: full-line AND inline. A first version only
        # dropped full-line comments, so `true # python -c "..."` still yielded
        # the gate code from behind the `#` -- the test then ran the ORIGINAL
        # logic instead of the mutated (dead) one. Measured: K1d survived.
        active = []
        for line in block.splitlines():
            code = line.split("#", 1)[0]
            if code.strip():
                active.append(code)
        active_text = "\n".join(active)
        m = re.search(r"python -c \"(.*)\"", active_text)
        assert m, "could not locate the gate's python -c command in the wheelhouse block"
        # as_posix, not str: on Windows a tmp path contains backslashes, which
        # become escape sequences inside the gate's single-quoted glob string
        # (measured: SyntaxError -> returncode 1 -> the test passed WITHOUT
        # testing the gate at all, on every input including the mutants).
        gate_code = m.group(1).replace("/opt/wheelhouse", tmp_path.as_posix())
        fake_dir = tmp_path
        (fake_dir / "matplotlib-3.5.3-py3-none-any.whl").write_text("fake", encoding="utf-8")
        (fake_dir / "six-1.17.0-py2.py3-none-any.whl").write_text("fake", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-c", gate_code],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
        assert proc.returncode != 0, (
            "the build gate did not fire against a wheelhouse containing "
            "matplotlib-3.5.3 -- it would let an oracle image build"
        )
        assert "FATAL" in (proc.stderr or proc.stdout or ""), (
            "the gate fired but printed no FATAL diagnostic"
        )

    def test_no_target_package_is_preinstalled(self) -> None:
        """The six target repos must NOT be in the pre-install list.

        A released target package in site-packages is a LATER revision than the
        base commit -- it contains the fix. This is the same property
        test_no_released_target_package_is_importable checks in the built image;
        asserting it on the Dockerfile too means a regression is caught even
        without a Docker daemon.
        """
        # Only look at the RUN pip install block, not the whole file: the
        # wheelhouse section legitimately downloads matplotlib.
        preinstall = self._preinstall_block()
        for target in ("django", "sympy", "matplotlib", "scikit-learn", "sklearn",
                       "seaborn", "requests"):
            assert target not in preinstall, (
                f"{target!r} is pre-installed in the image, which would put a "
                f"released copy of a target package (containing the fix) in "
                f"site-packages"
            )

    # ---------------------------------------------------------------- stub (E)

    STUB_DIR = ROOT / "docker" / "pip_vendor_stub"
    STUB_SCANNER = ROOT / "docker" / "scan_pip_vendor_stub.py"

    def _run_stub_scanner(self, root: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.STUB_SCANNER), str(root)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )

    def test_the_vendored_stub_carries_no_oracle_pair(self) -> None:
        """The stub's CONTENT is load-bearing, so it is checked, not trusted.

        The whole fix for the vendored-oracle finding rests on this directory: it
        replaces pip's vendored `requests`, which is a complete code oracle for
        any instance whose base commit is an older requests (measured on
        psf__requests-1963: 2/2 fix lines readable from the vendored copy, 0/2
        from the checkout). Measured gap this closes: restoring those two gold
        lines into docker/pip_vendor_stub/requests/session.py left the suite
        GREEN (51 passed), because no test mentioned the stub at all -- and the
        Dockerfile's own assertion only read `__init__.py`.

        This runs the SAME scanner the build runs (docker/scan_pip_vendor_stub.py)
        against the stub SOURCE, so a bad edit fails here without a daemon. It is
        a contiguity test (comment + assignment near each other), not a substring
        check, because the stub legitimately explains the oracle it replaces.
        """
        proc = self._run_stub_scanner(self.STUB_DIR)
        assert proc.returncode == 0, (
            f"the pip-vendored stub contains the requests oracle (pair): "
            f"{proc.stdout}{proc.stderr}"
        )

    def test_the_stub_scanner_is_not_vacuous(self, tmp_path: Path) -> None:
        """The scan must actually FIRE on the pair, or it is decoration.

        A scanner that never matched anything -- a broken path, an empty glob, a
        typo in the phrase -- would leave the test above passing forever. This
        restores the gold pair into a COPY of the stub and requires a non-zero
        exit, which is the mutation the reviewer used to show the stub was
        unguarded. It also pins that a LONE mention stays clean, so the scanner is
        not merely matching one line.
        """
        import shutil

        copy = tmp_path / "stub"
        shutil.copytree(self.STUB_DIR, copy)
        victim = copy / "requests" / "session.py"
        original = victim.read_text(encoding="utf-8")
        victim.write_text(
            original + "\n    # Override the original request.\n"
                       "    req = prepared_request\n",
            encoding="utf-8",
        )
        proc = self._run_stub_scanner(copy)
        assert proc.returncode != 0, (
            "the stub scanner did NOT fire on the restored gold pair, so the "
            "vendored oracle could come back with the suite still green"
        )
        assert "FATAL" in (proc.stdout + proc.stderr), (
            "the scanner fired without a FATAL diagnostic"
        )

        # Control: the same two phrases, split far apart, are not the fix.
        victim.write_text(
            original + "\n# Override the original request.\n" + "\n" * 12
            + "req = prepared_request\n",
            encoding="utf-8",
        )
        proc = self._run_stub_scanner(copy)
        assert proc.returncode == 0, (
            "the scanner flagged a NON-contiguous mention, so it is matching a "
            "single line and will false-positive on the stub's own prose"
        )

    def _run_commands(self) -> list[str]:
        """Every ACTIVE RUN command, with backslash continuations joined.

        Needed because the gate invocations span lines: the wheel gate is
        `RUN python /tmp/scan_pip_vendor_stub.py \\` + the targets on the next
        lines. A line-by-line scan would see the command name but not its targets,
        which is exactly how a check can look present while covering nothing.
        Comments are dropped so a commented-out RUN cannot satisfy any assertion.
        """
        commands: list[str] = []
        current: list[str] = []
        for raw in self._text().splitlines():
            if not current and raw.lstrip().startswith("#"):
                continue                      # a commented-out line never starts a RUN
            stripped = raw.rstrip()
            if stripped.endswith("\\"):
                current.append(stripped[:-1])
                continue
            current.append(stripped)
            joined = "\n".join(current).strip()
            if joined:
                commands.append(joined)
            current = []
        if current:
            commands.append("\n".join(current).strip())
        return commands

    def test_the_build_runs_the_stub_scanner(self) -> None:
        """The image build must RUN the scanner, not merely document the rule.

        Text in a Dockerfile cannot stop a future edit, so this asserts the
        invocation is present in an ACTIVE line and points at the real scanner.

        COUNTED, not merely found. Measured gap (reviewer, final audit): the
        scanner is invoked TWICE -- once for the site-packages stub and once for
        the ensurepip wheel -- and a test that only asked "is the name present in
        an active line" was satisfied by the `COPY` line, which names the same
        file. Deleting EITHER `RUN` therefore left the suite green, and deleting
        the wheel gate would silently stop scanning the path that was just found
        to carry the oracle. So the assertions below count real invocations.
        """
        active_text = "\n".join(self._run_commands())
        runs = [c for c in self._run_commands()
                if "scan_pip_vendor_stub.py" in c and c.lstrip().startswith("RUN")]
        assert runs, (
            "the Dockerfile no longer RUNS the stub scanner, so a stub carrying "
            "the oracle could be built into an image"
        )
        assert len(runs) >= 2, (
            f"expected the scanner to be run for BOTH targets (site-packages and "
            f"the ensurepip wheel), found {len(runs)} invocation(s): {runs}. "
            f"Deleting either RUN leaves the other path unscanned -- measured: the "
            f"wheel gate was removable while the suite stayed green."
        )
        assert "COPY docker/scan_pip_vendor_stub.py" in active_text, (
            "the scanner is invoked in the image but never copied into it"
        )
        assert self.STUB_SCANNER.exists(), (
            f"the Dockerfile runs {self.STUB_SCANNER.name} but it is missing"
        )

    def test_each_scanner_invocation_names_its_target(self) -> None:
        """Every MANDATORY target must be covered by some RUN -- not merely "unique".

        Counting invocations alone is not enough: two runs of the same path satisfy
        a count while leaving the other path unscanned, and checking only that the
        targets are "distinct" is satisfied by two runs of DIFFERENT non-mandatory
        paths. Measured (reviewer, final audit): pointing RUN1 at the ensurepip wheel
        left site-packages unscanned and the suite stayed GREEN -- the gate could be
        "two invocations, both unique" while missing a location that matters.

        So this pins the SET of mandatory targets, each required to appear in the
        arguments of a real RUN (read with continuations joined, so a target on a
        following line still counts).

        The set itself is asserted non-empty, and each target must be covered by a
        DISTINCT run. Both halves are load-bearing: an empty ``mandatory`` dict makes
        the loop below vacuous (measured: deleting both entries stayed GREEN), and
        one run mentioning both paths would let a second run be removed while the
        check still passed.
        """
        runs = [c for c in self._run_commands()
                if "scan_pip_vendor_stub.py" in c and c.lstrip().startswith("RUN")]
        assert runs, "no scanner RUN found at all"

        # The two locations measured to carry the oracle. BOTH are required: the
        # installed stub, and the pip wheel bundled inside ensurepip (a wheel is a
        # ZIP, so its source is readable without installing it).
        mandatory = {
            "the installed stub in site-packages":
                "/usr/local/lib/python3.9/site-packages/pip/_vendor/requests",
            "the ensurepip bundled pip wheel":
                "/usr/local/lib/python3.9/ensurepip/_bundled/pip-23.0.1-py3-none-any.whl",
        }
        assert mandatory, (
            "the mandatory target set is EMPTY, so the coverage loop below cannot "
            "fail -- a control that can be deleted by emptying its own list is not a "
            "control"
        )
        # The SIZE is pinned too, not only the loop. Measured (mutation): deleting a
        # single entry left the suite GREEN, because the loop below iterates whatever
        # the dict contains -- it cannot miss a target removed from its own list.
        assert len(mandatory) == 2, (
            f"the mandatory target set must name BOTH oracle locations (the installed "
            f"stub and the ensurepip wheel); it has {len(mandatory)}: {list(mandatory)}"
        )

        covering_runs = {}
        for label, target in mandatory.items():
            covered = [r for r in runs if target in r]
            assert covered, (
                f"no scanner RUN covers {label} ({target}). Every mandatory target "
                f"must be scanned at build time; runs found: {runs}"
            )
            covering_runs[label] = covered

        # Each target must be covered by a run of its OWN. Otherwise one RUN naming
        # both paths satisfies every entry, and deleting the other RUN goes
        # unnoticed -- the "two invocations" count still holds.
        assert len({id(r) for group in covering_runs.values() for r in group}) >= len(
            mandatory
        ), (
            "the mandatory targets are covered by fewer distinct RUN commands than "
            f"there are targets: {covering_runs}"
        )

    # --------------------------------------------- ensurepip's bundled wheel

    STRIPPER = ROOT / "docker" / "strip_ensurepip_oracle.py"

    def test_the_scanner_opens_archives(self, tmp_path: Path) -> None:
        """A wheel is a ZIP, so the scanner must look INSIDE archives.

        Measured: the base image ships a complete pip wheel via ensurepip and it
        carried the SAME gold fix as the site-packages copy
        (pip/_vendor/requests/sessions.py:259). A scanner that only walked
        directories reported that image clean, which is how the oracle stayed
        readable one directory over from the one that had been stripped.
        """
        import zipfile

        wheel = tmp_path / "pip-23.0.1-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as z:
            z.writestr(
                "pip/_vendor/requests/sessions.py",
                "def send(self):\n"
                "    # Override the original request.\n"
                "    req = prepared_request\n"
                "    return req\n",
            )
            z.writestr("pip/__init__.py", "version = '23.0.1'\n")
        proc = self._run_stub_scanner(wheel)
        assert proc.returncode != 0, (
            "the scanner reported an archive containing the gold pair as clean, "
            "so a wheel could carry the oracle into the image"
        )
        assert "FATAL" in (proc.stdout + proc.stderr)

        # Control: the same archive WITHOUT the pair must pass, or the check is
        # just failing on every archive.
        clean = tmp_path / "pip-23.0.1-clean-py3-none-any.whl"
        with zipfile.ZipFile(clean, "w") as z:
            z.writestr("pip/_vendor/requests/sessions.py", "def send(self):\n    return None\n")
        proc = self._run_stub_scanner(clean)
        assert proc.returncode == 0, (
            f"the scanner flagged a clean archive: {proc.stdout}{proc.stderr}"
        )

    def test_the_build_strips_the_ensurepip_wheel_and_checks_it(self) -> None:
        """The ensurepip wheel must be STRIPPED and then re-scanned, by active lines.

        Deleting the wheel is not an option: measured as root, `python -m venv`
        failed without it (ensurepip looks for that exact filename). So the build
        rewrites the archive in place and the scanner verifies the result. Both
        steps must be ACTIVE lines -- a commented-out gate cannot fail a build.
        """
        text = self._text()
        active = "\n".join(
            l for l in text.splitlines() if not l.strip().startswith("#")
        )
        # The RUN must be named, not just the COPY: a mutation that comments the
        # RUN out leaves the COPY line (and any bare-name check) intact, which
        # would make the wheel never stripped while the test stayed green.
        # Measured: an assertion on the bare filename survived that mutation.
        assert "RUN python /tmp/strip_ensurepip_oracle.py" in active, (
            "the Dockerfile no longer RUNS the stripper, so the oracle stays "
            "readable with zipfile inside pip-23.0.1-py3-none-any.whl"
        )
        assert "COPY docker/strip_ensurepip_oracle.py" in active, (
            "the stripper is invoked but never copied into the image"
        )
        assert self.STRIPPER.exists(), (
            f"the Dockerfile runs {self.STRIPPER.name} but it is missing"
        )
        # The wheel path must be named explicitly: the stripper cannot guess it,
        # and a silently-missing wheel would make the strip a no-op.
        assert "ensurepip/_bundled/pip-23.0.1-py3-none-any.whl" in active, (
            "the build no longer names the bundled pip wheel, so the strip step "
            "would not run against it"
        )

    def test_the_stripper_replaces_the_oracle_and_keeps_the_wheel_valid(
        self, tmp_path: Path
    ) -> None:
        """The stripper must be BEHAVIOURALLY correct, not merely present.

        It rewrites a real archive, so the properties that matter are: the pair is
        gone, the stub is in its place, RECORD still describes the archive (a
        stale RECORD makes the wheel malformed), and the wheel is still a ZIP that
        `zipfile` can read.
        """
        import base64
        import hashlib
        import zipfile

        wheel = tmp_path / "pip-23.0.1-py3-none-any.whl"
        record = "pip/_vendor/requests/sessions.py,sha256=STALE,999\n"
        with zipfile.ZipFile(wheel, "w") as z:
            z.writestr(
                "pip/_vendor/requests/sessions.py",
                "# Override the original request.\nreq = prepared_request\n",
            )
            z.writestr("pip/_vendor/requests/models.py", "class Request: pass\n")
            z.writestr("pip/_internal/cli.py", "def main(): pass\n")
            z.writestr("pip-23.0.1.dist-info/RECORD", record)

        proc = subprocess.run(
            [sys.executable, str(self.STRIPPER), str(wheel), str(self.STUB_DIR / "requests")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
        assert proc.returncode == 0, f"the stripper failed: {proc.stdout}{proc.stderr}"

        with zipfile.ZipFile(wheel) as z:
            names = z.namelist()
            assert not any(n.endswith("sessions.py") for n in names), (
                "the oracle-bearing sessions.py is still in the wheel"
            )
            vendored = [n for n in names if n.startswith("pip/_vendor/requests/")]
            assert vendored, "the stripper removed the vendored package entirely"
            # The stub, not real requests: its __init__ carries the marker.
            init = z.read("pip/_vendor/requests/__init__.py").decode("utf-8")
            assert "agentbench-stub" in init, (
                "the vendored package was not replaced with the source-free stub"
            )
            # RECORD must describe what is actually in the archive.
            rec_name = next(n for n in names if n.endswith("RECORD"))
            lines = [
                l for l in z.read(rec_name).decode("utf-8").splitlines()
                if l.startswith("pip/_vendor/requests/")
            ]
            assert lines, "RECORD no longer lists the vendored files"
            for line in lines:
                name, digest, size = line.rsplit(",", 2)
                blob = z.read(name)
                expected = base64.urlsafe_b64encode(
                    hashlib.sha256(blob).digest()
                ).rstrip(b"=").decode()
                assert digest == f"sha256={expected}", (
                    f"RECORD hash is stale for {name}, so the wheel is malformed"
                )
                assert int(size) == len(blob), f"RECORD size is stale for {name}"
            # Untouched entries survive the rewrite.
            assert "pip/_internal/cli.py" in names

    def test_the_stripper_fails_loudly_when_it_would_no_op(self, tmp_path: Path) -> None:
        """A silent no-op would reopen the oracle, so a missing layout must FAIL.

        The stripper exists because a specific wheel carries a specific subtree. If
        the image's pip is ever bumped, that subtree moves -- and a stripper that
        quietly did nothing would leave the build green with the oracle readable.
        """
        import zipfile

        wheel = tmp_path / "pip-99.0.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as z:
            z.writestr("pip/_internal/cli.py", "def main(): pass\n")
            z.writestr("pip-99.0.0.dist-info/RECORD", "pip/_internal/cli.py,sha256=x,1\n")
        proc = subprocess.run(
            [sys.executable, str(self.STRIPPER), str(wheel), str(self.STUB_DIR / "requests")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
        assert proc.returncode != 0, (
            "the stripper reported success on a wheel with no vendored requests "
            "subtree, so it silently no-ops when the layout changes"
        )
        assert "FATAL" in (proc.stdout + proc.stderr)

    @pytest.mark.parametrize("pin,why", [
        ("setuptools<60", "matplotlib errors on pkg_resources warnings; sklearn needs distutils.msvccompiler"),
        ("pyparsing<3", "old matplotlib + new pyparsing aborts collection"),
        ("Cython<3", "the 2018-2019 .pyx sources do not cythonize under Cython 3"),
        ("numpy==1.23.5", "numpy.distutils was removed after 1.23"),
    ])
    def test_load_bearing_pins_are_present(self, pin: str, why: str) -> None:
        """The pin must be in the PRE-INSTALL list, where the repos need it.

        Checking the whole file would pass on the wheelhouse copy of the same
        string -- measured: dropping `setuptools<60` from the pre-install list left
        the suite green until this was scoped to the block.
        """
        assert pin in self._preinstall_block(), (
            f"the pin {pin!r} was removed from the PRE-INSTALL list in the "
            f"Dockerfile. It is load-bearing: {why}. Removing it makes the "
            f"corresponding repo's tests fail at collection/import time."
        )

    def test_git_is_not_installed(self) -> None:
        """git's ABSENCE is the history-leak defence, so it must not creep in.

        A natural mistake is adding `git` to the apt list while adding build tools.
        The prompt, the .git mask, and the fairness analysis all assume it is gone.
        """
        text = self._text()
        apt_block = text.split("apt-get install")[1].split("&&")[0]
        assert "git" not in apt_block, (
            f"git was added to the apt install list ({apt_block.strip()[:80]!r}); "
            f"its absence is load-bearing for the history-leak defence"
        )

    def test_pip_conf_blocks_the_index(self) -> None:
        """Offline pip must be enforced by config, not by the agent remembering.

        Without `no-index = true` a bare `pip install x` would try the network and
        hang until the 180s timeout instead of failing fast.
        """
        text = self._text()
        assert "no-index = true" in text
        assert "find-links = /opt/wheelhouse" in text


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

#: The six repos under test, by their wheel/import names. A RELEASED copy of any
#: of these is an oracle: it is a later revision than the base commit, so it
#: contains the fix the agent is asked to write. Module level because two tests
#: need it (the wheelhouse check and the site-packages check).
_TARGET_PACKAGES = (
    "django", "sympy", "matplotlib", "seaborn", "sklearn", "scikit-learn",
    "scikit_learn", "requests",
)


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
        """git is NOT installed: the agent must use the host-side git_diff tool.

        Also load-bearing for the FAIRNESS analysis, so this test guards more than
        history leakage: the residual `.git` channels that reset_working_tree does
        not detect (a marker hidden in a commit message, a tag name, a branch name,
        a stash message, a blob in objects/) all require RUNNING git. With git
        absent they are unreachable from the container, which is why they are
        accepted as low risk rather than chased with more aggressive hashing that
        would re-introduce false positives on legitimate commits. If this test ever
        fails, those channels become reachable again.
        """
        proc = self._run(self.IMAGE, "sh", "-c", "command -v git || echo GIT_ABSENT")
        assert "GIT_ABSENT" in proc.stdout, (
            "git was found in the sandbox image; the design assumes it is absent "
            f"(so `git log` cannot leak history). got: {proc.stdout!r}"
        )

    def test_no_target_package_wheel_in_the_image(self) -> None:
        """K-1 (CRITICAL): no file in /opt/wheelhouse may be a target package.

        A wheel is a ZIP, so `zipfile` reads a target package's source WITHOUT
        installing it, without network and without the checkout. The previous image
        shipped `matplotlib-3.5.3-*.whl`, and measured against the 50 dataset
        instances: 10/50 had grading-test lines readable out of it, 4/50 were a
        COMPLETE oracle (100% of the test_patch's added lines), and 4/10 matplotlib
        instances had the FIX readable from the wheel's own non-test modules.
        matplotlib 3.5.3 is a LATER release than every base commit in the dataset,
        so it contains the very fixes the agent is asked to write.

        Asserts on the DIRECTORY CONTENTS, not on an import: the whole point of the
        leak was that importing is unnecessary. A name check is what catches it.
        """
        proc = self._run(self.IMAGE, "sh", "-lc", "ls -1 /opt/wheelhouse/")
        assert proc.returncode == 0, f"wheelhouse missing or unreadable: {proc.stderr}"
        files = [l.strip().lower() for l in proc.stdout.splitlines() if l.strip()]
        assert files, "the wheelhouse is empty; dependencies were expected in it"
        offenders = [f for f in files
                     if any(f.startswith(t) for t in _TARGET_PACKAGES)]
        assert not offenders, (
            f"target package wheel(s) in /opt/wheelhouse: {offenders}. A wheel is a "
            f"ZIP -- zipfile reads it without installing, so this is an oracle: the "
            f"released copy is a LATER revision than the base commit and contains "
            f"the fix. Remove it from the Dockerfile wheelhouse."
        )

    def test_no_target_package_in_site_packages(self) -> None:
        """The other place a released target package could hide.

        `pip install` writes site-packages, so an image that pre-installs a target
        package leaks there instead -- and that copy is readable with plain `cat`,
        which is even easier than opening a ZIP. Measured on the current image: the
        directory does not exist for any of the six.
        """
        code = (
            "import os, site\n"
            "sp = site.getsitepackages()[0]\n"
            "found = [t for t in "
            "['django','sympy','matplotlib','seaborn','sklearn','scikit_learn','requests']\n"
            "         if os.path.isdir(os.path.join(sp, t))]\n"
            "print('FOUND:' + (','.join(found) or 'NONE'))\n"
        )
        proc = self._run(self.IMAGE, "python", "-c", code)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "FOUND:NONE", (
            f"a target package is installed in site-packages, so its released "
            f"source (including its tests, for matplotlib) is readable with plain "
            f"`cat`: {proc.stdout!r}"
        )

    def test_a_wheel_is_readable_without_installing(self) -> None:
        """Proves the THREAT MODEL, so the tests above cannot be dismissed as strict.

        This shows that zipfile reads a wheel's source with no install, no network
        and no checkout -- using a dependency wheel that is legitimately present.
        If this ever fails, the reasoning behind the two tests above has changed and
        they should be revisited.
        """
        code = (
            "import glob, zipfile\n"
            "w = sorted(glob.glob('/opt/wheelhouse/*.whl'))\n"
            "assert w, 'no wheels to test with'\n"
            "z = zipfile.ZipFile(w[0])\n"
            "py = [n for n in z.namelist() if n.endswith('.py')]\n"
            "assert py, 'no .py in wheel'\n"
            "src = z.read(py[0]).decode('utf-8', 'replace')\n"
            "print('READ:' + py[0] + ':' + str(len(src)))\n"
        )
        proc = self._run(self.IMAGE, "python", "-c", code)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip().startswith("READ:"), (
            f"could not read a wheel's source without installing it: {proc.stdout!r}"
        )

    def test_the_checkout_still_imports_without_the_target_wheel(self) -> None:
        """The cost of removing the wheel must be ZERO for the repo under test.

        matplotlib's 10 instances import matplotlib from the CHECKOUT
        (PYTHONPATH=/work:/work/lib), not from a wheel, so removing the wheel must
        not break them. Measured: 11 passed. This guards against "fixing" the leak
        by removing matplotlib support entirely.
        """
        # Use a synthetic checkout with a marker, so this needs no dataset.
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lib = root / "lib" / "matplotlib"
            lib.mkdir(parents=True)
            (lib / "__init__.py").write_text("MARKER = 'checkout'\n", encoding="utf-8")
            (root / "lib" / "mpl_toolkits").mkdir(parents=True, exist_ok=True)
            (root / "lib" / "mpl_toolkits" / "__init__.py").write_text("", encoding="utf-8")

            proc = self._run(
                "-v", f"{Path(td).resolve()}:/work",
                "--tmpfs", "/work/.git",
                "-w", "/work", self.IMAGE, "python", "-c",
                "import matplotlib; print('OK:' + str(getattr(matplotlib,'MARKER','?')))",
            )
            assert proc.returncode == 0, (
                f"the checkout's own package could not be imported once the target "
                f"wheel was removed: {proc.stdout!r} {proc.stderr!r}"
            )
            assert "OK:checkout" in proc.stdout, proc.stdout

    def test_no_released_target_package_is_importable(self) -> None:
        """A released target package in site-packages IS the oracle.

        CHANGED MEANING (2026-10-08, operator decision). This test used to assert
        that the target packages were absent ENTIRELY -- the image carried pytest
        and nothing else. That made every test run die on ModuleNotFoundError, so
        `run_tests` was a dead end. The operator opened the sandbox to test
        running, so the assertion had to be re-aimed rather than deleted: what
        must stay true is narrower and more important.

        The oracle is not "a package named numpy is present". It is "a RELEASED
        copy of a package the agent is asked to fix is present", because a
        released copy is a LATER revision than the base commit -- it contains the
        fix. Dependencies are different: numpy does not contain Django's fix, so
        pre-installing it leaks nothing.

        So this asserts the property that actually matters, and asserts it the way
        the leak would occur: with NO checkout mounted, none of the six target
        packages may be importable. Measured in the shipped image:
        ``TARGET_IMPORTABLE: NONE``.
        """
        code = (
            "import importlib\n"
            "found = []\n"
            "for p in ['django','sympy','matplotlib','sklearn','seaborn','requests']:\n"
            "    try:\n"
            "        importlib.import_module(p)\n"
            "        found.append(p)\n"
            "    except ImportError:\n"
            "        pass\n"
            "print('IMPORTABLE:' + (','.join(found) or 'NONE'))\n"
        )
        # NOTE: no -v, so /work is empty. A mount would legitimately put the
        # checkout on the path (PYTHONPATH=/work) and that is tested separately.
        proc = self._run(self.IMAGE, "python", "-c", code)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "IMPORTABLE:NONE", (
            f"a RELEASED target package is importable without any checkout mounted, "
            f"so it would shadow the code under test with a later revision "
            f"containing the fix: {proc.stdout!r}"
        )

    def test_dependencies_ARE_importable(self) -> None:
        """The other half: deps must be present, or a test run cannot start.

        Without this, "delete every package" would satisfy the oracle test above
        while making the sandbox useless again -- the exact state the operator
        asked to fix. Measured failures before the change: django MISSING_MODULE,
        sympy MISSING_MODULE(mpmath), matplotlib MISSING_MODULE(numpy), seaborn
        MISSING_MODULE(numpy).

        Names here are IMPORT names, which are not always the distribution name:
        the distribution is `pillow` but the module is `PIL`. A first version of
        this test used the distribution names and failed on 'pillow' while the
        package was correctly installed -- the test was wrong, not the image.
        """
        code = (
            "import importlib\n"
            "missing = []\n"
            "for p in ['pytest','numpy','pandas','scipy','pytz','sqlparse','mpmath','PIL']:\n"
            "    try:\n"
            "        importlib.import_module(p)\n"
            "    except ImportError:\n"
            "        missing.append(p)\n"
            "print('MISSING:' + (','.join(missing) or 'NONE'))\n"
        )
        proc = self._run(self.IMAGE, "python", "-c", code)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "MISSING:NONE", (
            f"a dependency needed to RUN the tests is absent, so run_tests cannot "
            f"produce a regression signal: {proc.stdout!r}"
        )

    def test_the_checkout_shadows_the_wheelhouse_copy(self, tmp_path: Path) -> None:
        """matplotlib is in the wheelhouse AS A DEPENDENCY; the checkout must win.

        seaborn declares matplotlib as a dependency, so a released matplotlib must
        be installable -- the brief allows a target package "ONLY as a dependency".
        The risk that creates: when matplotlib IS the repo under test, the released
        wheel could shadow the code the agent is editing, handing it the fix.

        This pins the resolution order, which is what makes that safe. Measured:
        after installing the released wheel, ``import matplotlib`` still resolves
        to ``/work/lib/matplotlib/__init__.py``.
        """
        pkg = tmp_path / "lib" / "matplotlib"
        pkg.mkdir(parents=True)
        # A marker only the checkout could supply.
        (pkg / "__init__.py").write_text(
            "CHECKOUT_MARKER = 'from-the-checkout'\n__version__ = '0.0.0+checkout'\n",
            encoding="utf-8",
        )
        (tmp_path / "lib" / "mpl_toolkits").mkdir(parents=True, exist_ok=True)
        (tmp_path / "lib" / "mpl_toolkits" / "__init__.py").write_text("", encoding="utf-8")

        proc = self._run(
            "-v", f"{tmp_path.resolve()}:/work",
            "--tmpfs", "/work/.git",
            "-w", "/work", self.IMAGE, "sh", "-lc",
            "pip install -q --no-index --find-links=/opt/wheelhouse matplotlib 2>&1 | tail -1; "
            "python -c \"import matplotlib; "
            "print('RESOLVED:' + matplotlib.__file__); "
            "print('MARKER:' + getattr(matplotlib, 'CHECKOUT_MARKER', 'ABSENT'))\"",
            timeout=300,
        )
        assert "RESOLVED:/work/" in proc.stdout, (
            f"matplotlib resolved OUTSIDE the checkout, so the agent would be "
            f"reading a released revision instead of the code under test: "
            f"{proc.stdout!r}"
        )
        assert "MARKER:from-the-checkout" in proc.stdout, (
            f"the released wheel shadowed the checkout's package: {proc.stdout!r}"
        )

    def test_target_oracle_packages_are_absent(self) -> None:
        """SUPERSEDED by test_no_released_target_package_is_importable.

        Kept as a named pointer rather than deleted, because the OLD name still
        describes a real trap for anyone reading the suite: someone who sees only
        the old test's title might "restore" the empty image and re-break test
        running. The property is now asserted by the two tests above, which are
        narrower (released copies only) and cover the dependency half too.
        """
        pytest.skip(
            "superseded by test_no_released_target_package_is_importable: the "
            "oracle is a RELEASED copy of a target package, not the presence of "
            "any package. The empty image this test used to assert is what made "
            "run_tests a dead end."
        )

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

    def test_container_write_to_git_does_not_reach_the_host(self, tmp_path: Path) -> None:
        """A write to `.git` inside the container must NOT land on the host.

        `/work/.git` is masked with --tmpfs, so the container gets a throwaway
        directory. Verified end-to-end with a real marker: the host file must be
        unchanged. Without the mask a container run could persist anything into the
        host checkout's git metadata.
        """
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        sentinel = git_dir / "COMMIT_EDITMSG"
        sentinel.write_text("HOST_ORIGINAL\n", encoding="utf-8")
        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

        proc = self._run(
            "-v", f"{tmp_path.resolve()}:/work", "--tmpfs", "/work/.git",
            "-w", "/work", self.IMAGE, "sh", "-lc",
            "echo CONTAINER_WROTE_THIS > .git/COMMIT_EDITMSG; "
            "cat .git/COMMIT_EDITMSG",
        )
        assert proc.returncode == 0, proc.stderr
        assert "CONTAINER_WROTE_THIS" in proc.stdout, (
            "precondition: the container should be able to write to its own .git"
        )
        assert sentinel.read_text(encoding="utf-8") == "HOST_ORIGINAL\n", (
            "a container write to .git LANDED on the host checkout, so the tmpfs "
            "mask is not working"
        )

    def test_host_filesystem_is_not_mounted_without_v(self) -> None:
        proc = self._run(self.IMAGE, "sh", "-c", "ls /host_mnt 2>&1; ls /mnt 2>&1")
        assert "No such file or directory" in proc.stdout or proc.stdout.strip().endswith("total 0"), (
            f"/host_mnt unexpectedly accessible: {proc.stdout!r}"
        )

    def test_the_checkout_gets_a_real_git_dir_to_mask(self, tmp_path: Path) -> None:
        """Precondition for the mask probes: the mounted .git must be NON-empty.

        Every .git probe asserts that something is ABSENT. If the fixture's .git
        were empty to begin with, those probes would pass for the wrong reason and
        the mask could be removed without a single failure. This builds a populated
        .git (objects/ + packed-refs + HEAD) and proves the container sees none of
        it, while the working tree stays readable.
        """
        git_dir = tmp_path / ".git"
        (git_dir / "objects" / "ab").mkdir(parents=True)
        (git_dir / "objects" / "ab" / "cdef1234").write_text("blob", encoding="utf-8")
        (git_dir / "objects" / "pack").mkdir(parents=True)
        (git_dir / "packed-refs").write_text(
            "# pack-refs with: peeled fully-peeled\n"
            "1111111111111111111111111111111111111111 refs/heads/main\n"
            "2222222222222222222222222222222222222222 refs/tags/v9.9.9\n",
            encoding="utf-8",
        )
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (tmp_path / "app.py").write_text("print('tree readable')\n", encoding="utf-8")

        proc = self._run(
            "-v", f"{tmp_path.resolve()}:/work",
            "--tmpfs", "/work/.git",
            "-w", "/work", self.IMAGE, "sh", "-lc",
            "ls /work/.git/objects 2>&1; "
            "cat /work/.git/packed-refs 2>&1; "
            "cat /work/.git/HEAD 2>&1; "
            "echo '=== tree ==='; cat /work/app.py",
        )
        out = proc.stdout
        for leaked, why in [
            ("cdef1234", "a loose object"),
            ("refs/heads/main", "a branch ref"),
            ("refs/tags/v9.9.9", "a tag ref"),
            ("refs/heads", "the HEAD ref"),
        ]:
            assert leaked not in out, (
                f"{why} ({leaked!r}) was readable inside the container, so the "
                f"tmpfs mask on /work/.git is not working: {out!r}"
            )
        assert "tree readable" in out, (
            f"the working tree must stay readable while .git is masked: {out!r}"
        )

    def test_results_and_env_are_not_reachable(self, tmp_path: Path) -> None:
        """The two other leak channels the operator named must stay closed.

        The container mounts ONLY the checkout ({root}:/work), where root is
        ``datasets/repos/<org>/<repo>/<commit>``. ``results/`` lives at the project
        root, well outside that, so it is unreachable by construction -- but
        "unreachable by construction" is exactly the claim that should be probed,
        because a future change to the mount could make it reachable.

        Measured in the shipped image: all four probes report "No such file".
        """
        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
        proc = self._run(
            "-v", f"{tmp_path.resolve()}:/work",
            "--tmpfs", "/work/.git",
            "-w", "/work", self.IMAGE, "sh", "-lc",
            "ls /work/results 2>&1; "
            "ls /results 2>&1; "
            "cat /work/.env 2>&1; "
            "cat /.env 2>&1; "
            "echo END",
        )
        out = proc.stdout
        assert "END" in out, proc.stderr
        # No real path may resolve. A bare "No such file" per probe is the pass.
        assert out.count("No such file") >= 4, (
            f"one of results/ or .env resolved inside the container: {out!r}"
        )
        assert "predictions" not in out and "API_KEY" not in out, (
            f"a results/ or .env artifact was readable: {out!r}"
        )
