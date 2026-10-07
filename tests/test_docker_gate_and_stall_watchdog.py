"""Docker gate (P0) + SANDBOX_DOWN (P0) + stall watchdog (P2).

Three independent fixes from the 2026-10-07 investigation
(docs/INVESTIGASI_HANG_20261007.md). Each one has a test that was verified to go
RED when the fix is reverted -- see the mutation section of the task report.

The two incidents, for the reader who has not seen the investigation:

1. **Docker was down for a whole session.** Every `run_tests` call in all 9 runs
   of EXP-20261007-003 returned "[error] Docker daemon is not running or
   unreachable.", so the agents worked with no ability to run a single test --
   and every run was still recorded VALID. Nothing checked Docker before the
   sweep, and nothing noticed afterwards. Contamination was found by hand.

2. **A run hung for ~3 hours, undetected.** One HTTP request never returned. The
   per-read `API_TIMEOUT` cannot bound a server that keeps sending bytes, and
   with `ACT_TIMEOUT_SECONDS=0` there was no backstop at all. The process sat at
   CPU 0 with no log line until it was killed manually.

The fixes are deliberately narrow. Each test below asserts BOTH that the fix
fires on the bad case AND that it does not fire on the healthy case -- because a
control that fires on healthy runs is worse than no control: it discards good
data and discredits the alarm.
"""
from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from agents.tools import is_sandbox_infrastructure_error  # noqa: E402
from experiments.observability import _result_status  # noqa: E402
from experiments.runner import (  # noqa: E402
    _PATCH_STATUS_FAILED,
    _is_infrastructure_failure,
    sandbox_down_reason,
)
from models.inference import InferenceResult, InferenceRun  # noqa: E402
from models.result import (  # noqa: E402
    CostSummary,
    EvaluationResult,
    ExecutionResult,
    ExperimentResult,
)
from providers.tool_loop import (  # noqa: E402
    StalledError,
    _call_with_stall_guard,
    _is_context_overflow,
)


def _load_preflight():
    """Import tools/preflight_repos.py by path (tools/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "preflight_repos_under_test", ROOT / "tools" / "preflight_repos.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_sweep():
    """Import tools/run_final_sweep.py by path (tools/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "run_final_sweep_under_test", ROOT / "tools" / "run_final_sweep.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The VERBATIM error text from EXP-20261007-003's trajectories. Pinned as a
# literal, not imported from the source: if the message is reworded, this test
# must fail so the detector is updated with it. A test that imported the string
# from the code would pass no matter how the wording changed.
REAL_DAEMON_ERROR = (
    "[error] Docker daemon is not running or unreachable.\n"
    "error during connect: this error may indicate that the docker daemon is "
    "not running.: Get \"http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.24/"
    "containers/create\": open //./pipe/dockerDesktopLinuxEngine: The system "
    "cannot find the file specified."
)
REAL_IMAGE_ERROR = (
    "[error] Docker image 'agentbench-sandbox:latest' not found locally. "
    "Preflight requires image to be pre-built."
)


# ===========================================================================
# T1 -- the Docker gate must FAIL HARD
# ===========================================================================

class TestDockerGate:
    """The gate is only worth having if it can actually stop a sweep."""

    def test_passes_when_docker_is_healthy(self, monkeypatch):
        """A working Docker must NOT block the sweep (the other half of the fix)."""
        pf = _load_preflight()
        monkeypatch.setattr(
            pf, "_run_docker", lambda args, timeout: (0, "Server Version: 28.3.2"))
        ok, problems = pf.check_docker()
        assert ok is True, f"a healthy Docker was rejected: {problems}"
        assert problems == []

    def test_daemon_down_is_a_hard_failure(self, monkeypatch):
        """The exact incident: daemon down must be fatal, not a warning."""
        pf = _load_preflight()
        monkeypatch.setattr(
            pf, "_run_docker",
            lambda args, timeout: (1, "error during connect: docker daemon is not running"))
        ok, problems = pf.check_docker()
        assert ok is False, "a down daemon did not fail the gate"
        assert any("daemon" in p.lower() for p in problems)

    def test_missing_image_is_a_hard_failure(self, monkeypatch):
        pf = _load_preflight()

        def fake(args, timeout):
            if args == ["info"]:
                return 0, "Server Version: 28.3.2"
            return 1, f"No such image: {pf.SANDBOX_IMAGE}"

        monkeypatch.setattr(pf, "_run_docker", fake)
        ok, problems = pf.check_docker()
        assert ok is False, "a missing sandbox image did not fail the gate"
        assert any("image" in p.lower() for p in problems)

    def test_container_that_will_not_start_is_a_hard_failure(self, monkeypatch):
        """NOT redundant with daemon+image: both can pass while run still fails.

        This is the case a two-check gate would miss, and it is why the trivial
        container run exists.
        """
        pf = _load_preflight()

        def fake(args, timeout):
            if args == ["info"]:
                return 0, "Server Version: 28.3.2"
            if args[:2] == ["image", "inspect"]:
                return 0, "sha256:abc"
            return 1, "failed to create task: no such file or directory"

        monkeypatch.setattr(pf, "_run_docker", fake)
        ok, problems = pf.check_docker()
        assert ok is False, "a container that cannot start did not fail the gate"
        assert any("container" in p.lower() for p in problems)

    def test_missing_docker_cli_is_a_hard_failure(self, monkeypatch):
        pf = _load_preflight()
        monkeypatch.setattr(
            pf, "_run_docker",
            lambda args, timeout: (1, "docker CLI executable not found in PATH"))
        ok, _ = pf.check_docker()
        assert ok is False

    def test_main_exits_nonzero_and_runs_before_the_dataset_load(self, monkeypatch):
        """End-to-end: main() must exit non-zero WITHOUT touching the dataset.

        Failing fast matters because the dataset load is slow; a gate that runs
        after it wastes minutes before reporting a problem it could have caught
        immediately. ``load_instances`` is replaced with a tripwire.
        """
        pf = _load_preflight()
        monkeypatch.setattr(
            pf, "_run_docker",
            lambda args, timeout: (1, "error during connect: daemon not running"))

        def _tripwire(limit):
            raise AssertionError(
                "load_instances() ran -- the docker gate must come FIRST")

        monkeypatch.setattr(pf, "load_instances", _tripwire)
        monkeypatch.setattr(sys, "argv", ["preflight_repos.py", "--quiet"])

        with pytest.raises(SystemExit) as exc:
            pf.main()
        assert exc.value.code == 1, f"expected exit 1, got {exc.value.code}"

    def test_gate_has_no_bypass_flag(self):
        """A gate with a skip flag gets skipped -- which is how this happened."""
        src = (ROOT / "tools" / "preflight_repos.py").read_text(encoding="utf-8")
        for bypass in ("--skip-docker", "--no-docker", "--allow-no-docker"):
            assert bypass not in src, (
                f"{bypass} exists: a bypassable gate will be bypassed, and this "
                f"gate exists precisely because Docker went unchecked"
            )

    def test_sweep_still_gates_on_the_preflight(self):
        """The gate is useless if the sweep stops calling it."""
        src = (ROOT / "tools" / "run_final_sweep.py").read_text(encoding="utf-8")
        assert "preflight_repos" in src, "the sweep no longer runs the preflight"
        assert "PREFLIGHT FAILED" in src, "the sweep no longer aborts on failure"

    def test_sweep_aborts_when_the_preflight_fails(self, monkeypatch, capsys, tmp_path):
        """M4: a failing preflight must STOP the sweep, not merely be logged.

        The docker gate lives in the preflight, so this is the link that carries
        the P0 fix to the sweep: if the sweep ignored the return code, a down
        daemon would still launch 150 paid runs -- the original incident.

        Asserts BEHAVIOUR, not source text. An earlier version of this check only
        looked for the string "PREFLIGHT FAILED" in the file, so flipping
        ``if rc != 0:`` to ``if False:`` left it green: the message stayed in the
        source while the gate did nothing. Here the real ``main()`` runs and the
        launched command is intercepted.

        ``ROOT`` is redirected to a temp dir because ``main()`` writes
        ``logs/sweep_started.json``, which holds the REAL bill window that
        ``read_actual_bill.py --compare`` reconciles spend against. Under the
        mutation this test exists to catch, execution runs PAST the gate and would
        overwrite that file -- so the redirection is what keeps a mutation run from
        destroying the billing record.
        """
        mod = _load_sweep()
        monkeypatch.setattr(mod, "ROOT", tmp_path)
        launched: list[list[str]] = []

        def fake_call(cmd, **kwargs):
            # First call is the preflight; anything later is the sweep itself.
            launched.append(list(cmd))
            if any("preflight_repos" in str(a) for a in cmd):
                return 1          # the preflight FAILED
            raise AssertionError(
                f"the sweep was LAUNCHED despite a failing preflight: {cmd}")

        monkeypatch.setattr(subprocess, "call", fake_call)
        monkeypatch.setattr(
            mod, "select_issues", lambda limit: ["django__django-1"])
        monkeypatch.setattr(sys, "argv", ["run_final_sweep.py", "--limit", "1"])

        rc = mod.main()

        assert rc == 1, (
            f"a failing preflight must make main() return 1, got {rc!r}"
        )
        assert any("preflight_repos" in str(a) for a in launched[0]), (
            "the preflight was never invoked at all"
        )
        assert len(launched) == 1, (
            f"the sweep launched {len(launched) - 1} command(s) after the preflight "
            f"failed; it must abort before starting any run: {launched[1:]}"
        )
        assert "PREFLIGHT FAILED" in capsys.readouterr().out

    def test_sweep_continues_when_the_preflight_passes(self, monkeypatch, tmp_path):
        """The other half of M4: a PASSING preflight must not block the sweep.

        Without this, an implementation that always aborted would satisfy the
        test above while making the sweep unusable.

        ``ROOT`` is redirected here for the same reason as above: this path really
        does write the bill-window file.
        """
        mod = _load_sweep()
        monkeypatch.setattr(mod, "ROOT", tmp_path)
        launched: list[list[str]] = []

        def fake_call(cmd, **kwargs):
            launched.append(list(cmd))
            return 0

        monkeypatch.setattr(subprocess, "call", fake_call)
        monkeypatch.setattr(
            mod, "select_issues", lambda limit: ["django__django-1"])
        monkeypatch.setattr(sys, "argv", ["run_final_sweep.py", "--limit", "1"])

        rc = mod.main()

        assert rc == 0, f"a passing preflight must let the sweep run, got rc={rc}"
        assert len(launched) >= 2, (
            "the sweep was never launched after a passing preflight"
        )
        assert any("preflight_repos" in str(a) for a in launched[0])
        # The SECOND call is the sweep itself.
        assert any("run_with_env" in str(a) for a in launched[1]), (
            f"expected the sweep command second, got {launched[1]}"
        )


# ===========================================================================
# T2 -- SANDBOX_DOWN
# ===========================================================================

class TestSandboxDownDetection:
    """Detect the contamination automatically, without crying wolf."""

    def test_detects_the_real_daemon_error(self):
        assert is_sandbox_infrastructure_error(REAL_DAEMON_ERROR) is True

    def test_detects_the_real_image_error(self):
        assert is_sandbox_infrastructure_error(REAL_IMAGE_ERROR) is True

    @pytest.mark.parametrize("result", [
        "1 passed, 0 failed in 0.42s",
        "FAILED tests/test_x.py::test_a - AssertionError\n1 failed, 0 passed",
        "no tests ran in 0.01s",
        "[blocked] network transfer cmdlets are disabled in this sandbox",
        "[error] role 'reviewer' is read-only and may not modify the repository",
        "[error] cannot run command: FileNotFoundError",
        "[tests unavailable] The test environment for this repository is not installed",
        "[error] fatal: not a git repository",
        "[error] command timed out (180s limit)",
        "",
        # A test whose OUTPUT mentions docker/image must not be misread.
        "Docker is a container platform. 3 passed.",
        "image resize test passed",
    ])
    def test_does_not_fire_on_legitimate_results(self, result):
        """A false positive here discards a HEALTHY run -- the worse direction."""
        assert is_sandbox_infrastructure_error(result) is False, (
            f"false positive on a legitimate result: {result!r}"
        )

    def _inf(self, results, tool="run_tests"):
        return InferenceResult(
            role="direct", response="",
            tool_calls=[{"name": tool, "arguments": {}, "result": r}
                        for r in results],
        )

    def test_all_calls_infra_failed_is_sandbox_down(self):
        """The incident: every call hit the dead daemon."""
        reason = sandbox_down_reason([self._inf([REAL_DAEMON_ERROR] * 3)])
        assert reason, "the incident's pattern was not detected"
        assert "3 run_tests" in reason

    def test_single_call_infra_failed_is_sandbox_down(self):
        assert sandbox_down_reason([self._inf([REAL_DAEMON_ERROR])])

    def test_no_run_tests_calls_is_not_sandbox_down(self):
        """An agent that never ran a test is a legitimate outcome, not a fault."""
        assert sandbox_down_reason([self._inf([])]) == ""

    def test_all_calls_succeeded_is_not_sandbox_down(self):
        assert sandbox_down_reason([self._inf(["1 passed"] * 3)]) == ""

    def test_all_calls_failed_as_TESTS_is_not_sandbox_down(self):
        """A failing test is the sandbox WORKING. Must not be flagged."""
        assert sandbox_down_reason([self._inf(["1 failed, 0 passed"] * 2)]) == ""

    @pytest.mark.parametrize("results", [
        ["1 passed", "1 passed", REAL_DAEMON_ERROR],   # infra last
        [REAL_DAEMON_ERROR, "1 passed"],               # infra first
    ])
    def test_one_success_among_infra_errors_is_not_sandbox_down(self, results):
        """The sandbox demonstrably worked, so the run had real test feedback."""
        assert sandbox_down_reason([self._inf(results)]) == ""

    def test_non_run_tests_errors_are_ignored(self):
        """Only run_tests counts; another tool's error says nothing about Docker."""
        inf = InferenceResult(
            role="direct", response="",
            tool_calls=[
                {"name": "read_file", "arguments": {}, "result": "[error] not found"},
                {"name": "grep", "arguments": {}, "result": "[error] no matches"},
                {"name": "run_tests", "arguments": {}, "result": REAL_DAEMON_ERROR},
            ],
        )
        assert sandbox_down_reason([inf]), (
            "run_tests infra errors must still be counted when other tools also erred"
        )

    def test_sandbox_down_is_an_infrastructure_failure(self):
        """So --resume retries it instead of keeping contaminated data."""
        assert _is_infrastructure_failure({"patch_status": "SANDBOX_DOWN"}) is True
        assert "SANDBOX_DOWN" in _PATCH_STATUS_FAILED

    def test_sandbox_down_has_its_own_manifest_bucket(self):
        """It must be visible, not folded into EMPTY_PATCH."""
        res = ExperimentResult(
            instance_id="i", strategy="direct", model="m",
            execution=ExecutionResult(
                run=InferenceRun(patch="diff --git a/x b/x")),
            cost=CostSummary(input_cost_usd=0.0, output_cost_usd=0.0,
                             total_cost_usd=0.0),
            evaluation=EvaluationResult(success=True),
            patch_status="SANDBOX_DOWN",
        )
        assert _result_status(res) == "SANDBOX_DOWN"

    def test_detector_marker_matches_the_message_run_tests_returns(self):
        """M7: the detector's markers must match the text tools.py actually returns.

        The markers are matched against the RETURNED STRING, so a marker that no
        longer appears in any message the tool can produce is dead code -- the
        detector would silently stop firing. This pins the linkage from both ends:
        the wrapper message is produced by the source, and the marker that catches
        it is the one the reviewer verified is load-bearing.
        """
        src = (ROOT / "src" / "agents" / "tools.py").read_text(encoding="utf-8")

        # 1. The source must still emit the daemon message.
        assert "Docker daemon is not running or unreachable" in src, (
            "run_tests no longer returns the daemon-down message; the detector's "
            "marker for it is now dead"
        )

        # 2. The detector's marker must match that exact message.
        marker = "docker daemon is not running"
        emitted = "[error] Docker daemon is not running or unreachable."
        assert marker in emitted.lower(), (
            f"marker {marker!r} does not match the message run_tests emits"
        )
        assert is_sandbox_infrastructure_error(emitted) is True, (
            "the marker no longer fires on the message the tool returns"
        )

        # 3. And the marker must still be present in the module's marker list.
        from agents.tools import _SANDBOX_INFRA_MARKERS
        assert marker in _SANDBOX_INFRA_MARKERS, (
            f"the load-bearing marker {marker!r} was removed from "
            f"_SANDBOX_INFRA_MARKERS; the real daemon error would no longer be "
            f"detected"
        )

    def test_valid_runs_are_untouched_by_this_change(self):
        """The fix must not reclassify a normal run."""
        res = ExperimentResult(
            instance_id="i", strategy="direct", model="m",
            execution=ExecutionResult(
                run=InferenceRun(patch="diff --git a/x b/x")),
            cost=CostSummary(input_cost_usd=0.0, output_cost_usd=0.0,
                             total_cost_usd=0.0),
            evaluation=EvaluationResult(success=True),
            patch_status="VALID",
        )
        assert _result_status(res) == "PATCH_GENERATED"
        assert _is_infrastructure_failure({"patch_status": "VALID"}) is False

    def test_runner_records_SANDBOX_DOWN_end_to_end(self, tmp_path):
        """M11: a run whose every run_tests call died must be recorded SANDBOX_DOWN.

        Drives the REAL ``run_experiments`` and inspects the row it writes. The
        unit tests above only exercise ``sandbox_down_reason`` in isolation, so
        DELETING the assignment in the runner's success path left them green --
        the detection existed but nothing proved it reached the recorded status.

        The strategy returns a NON-EMPTY patch deliberately: that is what makes
        the run look like a normal success, and it is exactly why the incident
        went unnoticed. The patch must still be kept; only the status changes.
        """
        from experiments.runner import run_experiments
        from models.issue import Issue

        class _SandboxDownStrategy:
            """Every run_tests call fails at the daemon, but a patch is produced."""

            def run(self, issue):
                inf = InferenceResult(
                    role="direct",
                    response="diff --git a/x b/x",
                    tool_calls=[
                        {"name": "run_tests", "arguments": {"command": "pytest"},
                         "result": REAL_DAEMON_ERROR}
                        for _ in range(3)
                    ],
                )
                run = InferenceRun(patch="diff --git a/x b/x", inferences=[inf],
                                   messages=[])
                result = ExperimentResult(
                    instance_id=issue.instance_id,
                    strategy="direct",
                    model="test-model",
                    execution=ExecutionResult(run=run),
                    cost=CostSummary(input_cost_usd=0.0, output_cost_usd=0.0,
                                     total_cost_usd=0.0),
                    evaluation=EvaluationResult(success=True, error=""),
                )
                from models.patch import Patch
                return Patch(response="diff --git a/x b/x"), result

        issue = Issue(
            instance_id="django__django-9997",
            repo="django/django",
            base_commit="0" * 40,
            problem_statement="p",
        )
        run_experiments(
            [issue],
            {"direct": _SandboxDownStrategy()},
            base_dir=str(tmp_path),
            provider_name="test",
            model="test-model",
            rate_limit_seconds=0,
        )

        exp_dir = sorted(tmp_path.glob("EXP-*"))[-1]
        rows = [
            json.loads(line)
            for line in (exp_dir / "predictions" / "predictions.jsonl")
            .read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        assert len(rows) == 1, f"expected one row, got {rows}"
        assert rows[0]["patch_status"] == "SANDBOX_DOWN", (
            f"a run that could not execute a single test was recorded "
            f"{rows[0]['patch_status']!r}; this is the EXP-20261007-003 "
            f"contamination, which must be automatic"
        )
        # The patch is real work and must survive; only the status is changed.
        assert rows[0]["model_patch"].strip(), (
            "the patch was dropped; a SANDBOX_DOWN run keeps its patch so an "
            "evaluator can still score it"
        )

        # And the summary artifact must agree with the row.
        summary = json.loads(
            (exp_dir / "artifacts" / issue.instance_id / "direct" / "summary.json")
            .read_text(encoding="utf-8")
        )
        assert summary["patch_status"] == "SANDBOX_DOWN", (
            f"summary.json says {summary['patch_status']!r} but the row says "
            f"SANDBOX_DOWN -- the two records disagree"
        )

    def test_runner_keeps_VALID_when_the_sandbox_worked(self, tmp_path):
        """The other half of M11: a normal run must stay VALID.

        Without this, a detector that flagged everything would satisfy the test
        above while discarding every good run.
        """
        from experiments.runner import run_experiments
        from models.issue import Issue
        from models.patch import Patch

        class _HealthyStrategy:
            def run(self, issue):
                inf = InferenceResult(
                    role="direct",
                    response="diff --git a/x b/x",
                    tool_calls=[
                        {"name": "run_tests", "arguments": {"command": "pytest"},
                         "result": "3 passed in 0.4s"},
                        {"name": "run_tests", "arguments": {"command": "pytest -k x"},
                         "result": "1 failed, 0 passed"},
                    ],
                )
                run = InferenceRun(patch="diff --git a/x b/x", inferences=[inf],
                                   messages=[])
                result = ExperimentResult(
                    instance_id=issue.instance_id,
                    strategy="direct",
                    model="test-model",
                    execution=ExecutionResult(run=run),
                    cost=CostSummary(input_cost_usd=0.0, output_cost_usd=0.0,
                                     total_cost_usd=0.0),
                    evaluation=EvaluationResult(success=True, error=""),
                )
                return Patch(response="diff --git a/x b/x"), result

        issue = Issue(
            instance_id="django__django-9996",
            repo="django/django",
            base_commit="0" * 40,
            problem_statement="p",
        )
        run_experiments(
            [issue],
            {"direct": _HealthyStrategy()},
            base_dir=str(tmp_path),
            provider_name="test",
            model="test-model",
            rate_limit_seconds=0,
        )
        exp_dir = sorted(tmp_path.glob("EXP-*"))[-1]
        rows = [
            json.loads(line)
            for line in (exp_dir / "predictions" / "predictions.jsonl")
            .read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        assert rows[0]["patch_status"] != "SANDBOX_DOWN", (
            "a run whose tests actually executed was marked SANDBOX_DOWN"
        )
        assert rows[0]["patch_status"] == "VALID", (
            f"a healthy run was recorded as {rows[0]['patch_status']!r}"
        )


# ===========================================================================
# T3 -- the stall watchdog
# ===========================================================================

class TestStallGuard:
    """Bound SILENCE, never total duration. The distinction is the whole point."""

    def test_a_wedged_read_is_abandoned(self):
        """THE regression test for the 3-hour hang.

        A real TCP server accepts the request and then never replies -- the exact
        shape of the 20:11:56 incident. Without the guard this call blocks
        forever, so this test is what proves the incident can no longer happen.
        """
        import httpx

        holder: dict = {}
        stop = threading.Event()

        def server():
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", 0))
            srv.listen(4)
            holder["port"] = srv.getsockname()[1]
            srv.settimeout(20)
            try:
                conn, _ = srv.accept()
                conn.recv(65536)
                stop.wait(20)          # hold the connection open, never reply
                conn.close()
            except OSError:
                pass
            finally:
                srv.close()

        threading.Thread(target=server, daemon=True).start()
        while "port" not in holder:
            time.sleep(0.01)

        def wedged():
            # A large per-read timeout, to make the point: the read timeout
            # cannot bound this, so the guard is the only thing that can.
            with httpx.Client(timeout=httpx.Timeout(600.0, connect=5.0)) as c:
                return c.post(f"http://127.0.0.1:{holder['port']}/x", json={})

        t0 = time.perf_counter()
        with pytest.raises(StalledError):
            _call_with_stall_guard(wedged, 1.0, "tool_loop[direct] turn 25")
        elapsed = time.perf_counter() - t0
        stop.set()
        assert 0.8 < elapsed < 8, f"guard fired at the wrong time: {elapsed:.2f}s"

    def test_a_healthy_long_act_is_not_touched(self):
        """Activity every round, for far longer than the bound, must complete.

        Mirrors the reviewer's check: work every 5 min for 30 min against a 15 min
        bound. Scaled down (the ratio is what matters) so the suite stays fast:
        each round works for a fraction of the bound, but the TOTAL is a multiple
        of it -- so an implementation that bounded duration instead of silence
        would stop this and fail here.
        """
        bound, work, rounds = 0.2, 0.05, 12   # 12 x 0.05s = 0.6s = 3x the bound
        completed = 0
        t0 = time.perf_counter()
        for i in range(rounds):
            _call_with_stall_guard(
                lambda: (time.sleep(work), "ok")[1], bound, f"turn {i}")
            completed += 1
        elapsed = time.perf_counter() - t0

        assert completed == rounds, "a healthy long act was cut short"
        # The whole point: total duration EXCEEDS the bound and nothing stopped
        # it, because the bound applies to the silence between progress, not to
        # how long the act may run.
        assert elapsed > bound, (
            f"the test did not actually exceed the bound "
            f"(elapsed={elapsed:.2f}s, bound={bound}s), so it proves nothing"
        )

    def test_zero_disables_the_guard(self):
        """0 means off, matching ACT_TIMEOUT_SECONDS=0 semantics."""
        t0 = time.perf_counter()
        r = _call_with_stall_guard(lambda: (time.sleep(0.2), "done")[1], 0, "x")
        assert r == "done"
        assert time.perf_counter() - t0 >= 0.2, "the call did not run inline"

    def test_original_exception_type_is_preserved(self):
        """A real error must not be masked as a stall."""

        class Boom(RuntimeError):
            pass

        def raises():
            raise Boom("real error")

        with pytest.raises(Boom):
            _call_with_stall_guard(raises, 5.0, "x")

    def test_return_value_passes_through(self):
        assert _call_with_stall_guard(lambda: 42, 5.0, "x") == 42

    def test_stalled_error_is_not_a_context_overflow(self):
        """It must propagate to the runner, not take the recoverable stop path."""
        assert _is_context_overflow(StalledError("no response within 900s")) is False

    def test_a_stall_is_fatal_and_is_not_retried(self, monkeypatch):
        """M14: ONE stall must cost ONE bound, not three.

        The tool loop retries a failed request up to ``Config.MAX_RETRIES`` times.
        If a stall were retried like an ordinary error, one wedged connection
        would cost 3 x 900s = 45 minutes instead of 15 -- and every one of those
        attempts would block on the same wedged endpoint, so the sweep would lose
        the time without gaining anything.

        The bound is on the RETRY decision, so the assertion is the number of
        HTTP attempts: exactly 1. Under the mutation (StalledError dropped from
        ``fatal_on``) this counts 3.
        """
        from evaluation import retry as retry_mod
        from providers import tool_loop as tl_mod

        calls = {"n": 0}

        class _StallingCompletions:
            def create(self, **kwargs):
                calls["n"] += 1
                raise StalledError("no response within 900s")

        class _Client:
            def __init__(self):
                self.chat = type("Chat", (), {})()
                self.chat.completions = _StallingCompletions()

        # The assertion is the ATTEMPT COUNT, so the backoff sleeps are stubbed
        # out: they would add 2s + 4s to the suite for no extra coverage. Patched
        # on the retry module's own namespace so nothing else is affected.
        class _NoSleep:
            @staticmethod
            def sleep(_seconds):
                pass

        monkeypatch.setattr(retry_mod, "time", _NoSleep)
        monkeypatch.setattr(tl_mod.Config, "TOOL_OUTPUT_MAX_CHARS", 2000)

        with pytest.raises(StalledError):
            tl_mod.run_tool_loop(
                _Client(), model="fake", prompt="fix", role="direct",
                tools=[{"type": "function",
                        "function": {"name": "read_file", "parameters": {}}}],
                repo_root=None, max_tool_turns=5,
            )

        assert calls["n"] == 1, (
            f"the stalled request was attempted {calls['n']} time(s). A stall is "
            f"fatal -- retrying it multiplies one 900s bound into "
            f"{calls['n']} x 900s and blocks on the same wedged connection."
        )

    def test_an_ordinary_error_IS_still_retried(self, monkeypatch):
        """The other half of M14: fatal_on must not make EVERY error fatal.

        Without this, marking all exceptions fatal would satisfy the test above
        while removing the retry behaviour the loop depends on for transient
        provider blips.
        """
        from evaluation import retry as retry_mod
        from providers import tool_loop as tl_mod

        calls = {"n": 0}

        class _FlakyCompletions:
            def create(self, **kwargs):
                calls["n"] += 1
                raise ConnectionError("connection reset by peer")

        class _Client:
            def __init__(self):
                self.chat = type("Chat", (), {})()
                self.chat.completions = _FlakyCompletions()

        class _NoSleep:
            @staticmethod
            def sleep(_seconds):
                pass

        monkeypatch.setattr(retry_mod, "time", _NoSleep)
        monkeypatch.setattr(tl_mod.Config, "TOOL_OUTPUT_MAX_CHARS", 2000)

        with pytest.raises(ConnectionError):
            tl_mod.run_tool_loop(
                _Client(), model="fake", prompt="fix", role="direct",
                tools=[{"type": "function",
                        "function": {"name": "read_file", "parameters": {}}}],
                repo_root=None, max_tool_turns=5,
            )

        assert calls["n"] > 1, (
            f"a transient provider error was attempted only {calls['n']} time(s); "
            f"the retry path must still work for ordinary failures"
        )

    def test_stalled_is_an_infrastructure_failure(self):
        """Retryable: a stall says nothing about the strategy."""
        assert "STALLED" in _PATCH_STATUS_FAILED
        assert _is_infrastructure_failure({"patch_status": "STALLED"}) is True

    def test_stalled_has_its_own_manifest_bucket(self):
        res = ExperimentResult(
            instance_id="i", strategy="direct", model="m",
            execution=ExecutionResult(run=InferenceRun(patch="")),
            cost=CostSummary(input_cost_usd=0.0, output_cost_usd=0.0,
                             total_cost_usd=0.0),
            evaluation=EvaluationResult(success=False),
            patch_status="STALLED",
        )
        assert _result_status(res) == "STALLED"

    def test_runner_records_STALLED_when_an_act_stalls(self, tmp_path):
        """END-TO-END: a StalledError out of a strategy must be recorded STALLED.

        This is the test the mutation run demanded. The others only check the
        static ``_PATCH_STATUS_FAILED`` set, so DELETING the runner's
        ``isinstance(e, StalledError)`` branch left them all green -- the
        classification was unpinned. Here the real ``run_experiments`` failure
        path runs and the row it writes is inspected.
        """
        from experiments.runner import run_experiments
        from models.issue import Issue

        class _StallingStrategy:
            """Raises exactly what the tool loop raises on a wedged connection."""

            def run(self, issue):
                raise StalledError(
                    "tool_loop[direct] turn 25 produced no response within 900s")

        issue = Issue(
            instance_id="django__django-9999",
            repo="django/django",
            base_commit="0" * 40,
            problem_statement="p",
        )
        run_experiments(
            [issue],
            {"direct": _StallingStrategy()},
            base_dir=str(tmp_path),
            provider_name="test",
            model="test-model",
            rate_limit_seconds=0,
        )

        exp_dir = sorted(tmp_path.glob("EXP-*"))[-1]
        rows = [
            json.loads(line)
            for line in (exp_dir / "predictions" / "predictions.jsonl")
            .read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        assert len(rows) == 1, f"expected one recorded row, got {rows}"
        assert rows[0]["patch_status"] == "STALLED", (
            f"a stalled act was recorded as {rows[0]['patch_status']!r}; it must be "
            f"STALLED, or a wedged connection reads as an ordinary error"
        )
        assert rows[0]["error_type"] == "StalledError"

    def test_runner_still_labels_other_errors_correctly(self, tmp_path):
        """The STALLED branch must not swallow the pre-existing labels."""
        from experiments.runner import run_experiments
        from models.issue import Issue

        class _BoomingStrategy:
            def run(self, issue):
                raise RuntimeError("KeyError: 'path'")

        issue = Issue(
            instance_id="django__django-9998",
            repo="django/django",
            base_commit="0" * 40,
            problem_statement="p",
        )
        run_experiments(
            [issue],
            {"direct": _BoomingStrategy()},
            base_dir=str(tmp_path),
            provider_name="test",
            model="test-model",
            rate_limit_seconds=0,
        )
        exp_dir = sorted(tmp_path.glob("EXP-*"))[-1]
        rows = [
            json.loads(line)
            for line in (exp_dir / "predictions" / "predictions.jsonl")
            .read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        assert rows[0]["patch_status"] == "ERROR", (
            f"an ordinary bug is now labelled {rows[0]['patch_status']!r}; the "
            f"STALLED branch must be additive, not a catch-all"
        )

    def test_effective_bound_in_this_environment_is_900s(self):
        """The value the RUNNING process uses -- which .env may override.

        Named for what it actually checks. It was previously called
        ``test_default_bound_is_900s``, which was misleading: ``_get_int_env``
        reads ``os.getenv`` FIRST, and this repo's ``.env`` sets
        ``STALL_TIMEOUT_SECONDS=900``. So this test passes on the ENVIRONMENT
        value and would stay green even if the code's own default were changed to
        something dangerous. The source literal is pinned separately, below.
        """
        from config import Config
        assert Config.STALL_TIMEOUT_SECONDS == 900, (
            "the effective no-progress bound changed; it must stay above "
            "API_TIMEOUT (600) or it would fire on permitted requests"
        )

    def test_config_source_default_is_900(self):
        """M19/M20: pin the LITERAL default in config.py, not the effective value.

        Why this is a separate test from the one above: ``Config`` reads the
        environment before its own default, and ``.env`` sets this variable. So a
        mutation of the literal in the source --

            900 -> 300   (below API_TIMEOUT: fires on permitted slow requests)
            900 -> 0     (guard silently OFF on a clean clone with no .env)

        -- is INVISIBLE to any assertion on ``Config.STALL_TIMEOUT_SECONDS`` here.
        Both are dangerous in opposite directions, and a fresh clone is exactly
        the case the default exists for.

        Reads the SOURCE with a regex rather than importing, because importing
        cannot distinguish "the default" from "the override".
        """
        import re

        src = (ROOT / "src" / "config.py").read_text(encoding="utf-8")
        m = re.search(
            r'STALL_TIMEOUT_SECONDS\s*=\s*_get_int_env\(\s*'
            r'"STALL_TIMEOUT_SECONDS"\s*,\s*(\d+)\s*\)',
            src,
        )
        assert m, (
            "could not find the STALL_TIMEOUT_SECONDS default in config.py. If the "
            "definition was reformatted, update this regex -- do NOT delete this "
            "test, or the default becomes unpinned"
        )
        literal = int(m.group(1))
        assert literal == 900, (
            f"config.py's STALL_TIMEOUT_SECONDS default is {literal}, not 900. "
            f"900 was measured against 767 real requests (longest 598s); a smaller "
            f"value cuts healthy slow requests, and 0 disables the only guard "
            f"against a wedged connection on a clean clone."
        )
        # Guard the ordering invariant too, from the SOURCE: a default below
        # API_TIMEOUT would fire on requests the read timeout still permits.
        api = re.search(r'API_TIMEOUT\s*=\s*_get_int_env\(\s*'
                        r'"API_TIMEOUT"\s*,\s*(\d+)\s*\)', src)
        if api:
            assert literal > int(api.group(1)), (
                f"config.py defaults STALL_TIMEOUT_SECONDS ({literal}) <= "
                f"API_TIMEOUT ({api.group(1)}); the no-progress bound must exceed "
                f"the per-read timeout it exists to complement"
            )

    def test_bound_exceeds_api_timeout(self):
        """A bound below API_TIMEOUT would cut requests the read timeout allows.

        This is a RATIO check, not a magic-number check: whatever the two values
        are set to, the no-progress bound must leave room for a legitimate slow
        request.
        """
        from config import Config
        assert Config.STALL_TIMEOUT_SECONDS > Config.API_TIMEOUT, (
            f"STALL_TIMEOUT_SECONDS ({Config.STALL_TIMEOUT_SECONDS}) must exceed "
            f"API_TIMEOUT ({Config.API_TIMEOUT})"
        )


# ===========================================================================
# Regression: the wall-clock decision must NOT be reversed
# ===========================================================================

class TestNoWallClockBoundRegression:
    """T3 must not smuggle a wall-clock cap back in.

    ACT_TIMEOUT_SECONDS=0 was a deliberate decision: an unbounded run is honest
    data, a truncated run is corrupt data. The stall watchdog is a DIFFERENT
    control (silence, not duration) and adding it must not undo that decision.
    """

    def test_act_timeout_is_still_zero_in_the_sweep(self):
        src = (ROOT / "tools" / "run_final_sweep.py").read_text(encoding="utf-8")
        assert "ACT_TIMEOUT_SECONDS = 0" in src, (
            "the sweep's ACT_TIMEOUT_SECONDS is no longer 0 -- the stall watchdog "
            "was supposed to be an ADDITION, not a replacement for that decision"
        )

    def test_stall_timeout_is_pinned_in_the_sweep(self):
        """Left to .env, a stale value could silently disable the only guard."""
        src = (ROOT / "tools" / "run_final_sweep.py").read_text(encoding="utf-8")
        assert "STALL_TIMEOUT_SECONDS = 900" in src
        assert '"--set", f"STALL_TIMEOUT_SECONDS={STALL_TIMEOUT_SECONDS}"' in src, (
            "STALL_TIMEOUT_SECONDS is not passed to the run explicitly"
        )

    def test_the_guard_is_not_a_duration_check(self):
        """It must not compare TOTAL elapsed time against the bound.

        A guard that stopped an act after 900 s of TOTAL work would be exactly the
        wall-clock cap that was removed. The implementation must bound each
        request's silence instead.
        """
        src = (ROOT / "src" / "providers" / "tool_loop.py").read_text(encoding="utf-8")
        assert "worker.join(stall_seconds)" in src, (
            "the guard no longer bounds a single call's duration"
        )
