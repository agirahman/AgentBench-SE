"""The preflight gate must check the SAME batch the sweep is about to run.

Why it matters: a dirty checkout makes `git diff` capture changes the agent did
not make, which reads in the results as the agent solving the issue. The sweep
therefore GATES on the preflight rather than printing a reminder.

But the gate used to take its own default -- all 50 instances with a checkout --
regardless of `--limit`. So a 10-issue pilot was gated on 40 repositories it never
touches, and a green there said nothing about the 10 that would actually run. That
is the "control that does not cover the thing it claims to cover" failure: worse
than no control, because it is trusted (MEMORY, trap #20).

The fix passes --limit through, so both sides select "the first N by sorted id"
and the checked set IS the run set.

NOTE on how this is tested. An earlier version asserted the ARGUMENT-BUILDING
string appeared in the sweep's source. That test PASSED with the fix disabled --
commenting the branch out left the string in place -- so it was decoration, not a
check (MEMORY, trap #21). These tests instead RUN the sweep's argument builder and
intercept the subprocess, so the observable behaviour is what gets asserted.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SWEEP = ROOT / "tools" / "run_final_sweep.py"


def _load_sweep_module():
    """Import run_final_sweep by path (tools/ is not a package)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_final_sweep_under_test", SWEEP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def captured_preflight(monkeypatch):
    """Run `main()` for real and capture the argv it hands the preflight.

    ``subprocess.call`` is replaced, so nothing is executed and no repo is touched.

    The capture filters to the PREFLIGHT call specifically: main() also shells out
    to run_with_env.py for the sweep itself, which is invoked LAST. Returning the
    last call would assert against the sweep command instead of the gate -- and
    that mistake would still "pass" the negative tests for the wrong reason.
    """
    seen: list[list[str]] = []

    def fake_call(cmd, **kwargs):
        seen.append(list(cmd))
        return 0            # pretend the preflight passed

    monkeypatch.setattr(subprocess, "call", fake_call)

    mod = _load_sweep_module()
    # Do not depend on the dataset cache for the gate test.
    monkeypatch.setattr(mod, "select_issues", lambda limit: [f"inst-{i}" for i in range(limit or 3)])

    def run(*argv):
        seen.clear()
        monkeypatch.setattr(sys, "argv", ["run_final_sweep.py", *argv])
        mod.main()
        preflight_calls = [c for c in seen if any("preflight_repos" in str(a) for a in c)]
        assert len(preflight_calls) == 1, (
            f"expected exactly one preflight invocation, saw {len(preflight_calls)}: {seen}"
        )
        return preflight_calls[0]

    return run


def test_limit_is_passed_through_to_the_preflight(captured_preflight):
    """A limited sweep must gate a limited preflight."""
    cmd = captured_preflight("--limit", "10")
    assert "--limit" in cmd, (
        f"the gate would check every instance instead of the batch: {cmd}"
    )
    assert cmd[cmd.index("--limit") + 1] == "10"


def test_no_limit_checks_everything(captured_preflight):
    """A full sweep must still check all instances."""
    cmd = captured_preflight()
    assert "--limit" not in cmd


def test_explicit_issue_list_is_not_silently_limited(captured_preflight):
    """--issues overrides --limit, and cannot be expressed as 'first N'.

    Passing --limit anyway would check a set that is not the run set while looking
    like it does -- the exact failure this file exists to prevent.
    """
    cmd = captured_preflight("--limit", "5", "--issues", "django__django-11019")
    assert "--limit" not in cmd, (
        "an explicit id list must not be turned into 'first N' silently"
    )


def test_smoke_run_is_not_silently_limited(captured_preflight):
    """--smoke runs 1 issue; --limit would be a different 1 only by coincidence."""
    cmd = captured_preflight("--limit", "5", "--smoke")
    assert "--limit" not in cmd


def test_preflight_accepts_the_limit_flag():
    """The flag must exist on the other side, or the pass-through silently fails."""
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "preflight_repos.py"), "--help"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert "--limit" in r.stdout, "preflight_repos.py does not accept --limit"


def test_the_selection_is_the_same_on_both_sides():
    """Both tools currently pick the SAME N instances -- verified, not assumed.

    The --limit pass-through rests on this, so it is measured rather than argued:
    the sweep sorts by instance_id, while the preflight iterates the dataset in its
    given order (preflight_repos.load_instances does NOT sort). Those would differ
    in general, but on the real SWE-bench Lite dataset both yield the same first N
    -- checked directly, 50 of 50 and the first 10 identical.

    This test pins the CURRENT agreement, not a guarantee. If the dataset order
    ever changes, it will fail and the fix must pass an explicit ID LIST instead of
    a count. That caveat is the point: the pass-through is correct today by
    measurement, and this test is what will notice if that stops being true.
    """
    from datasets import load_dataset

    sweep_src = SWEEP.read_text(encoding="utf-8")
    pf_src = (ROOT / "tools" / "preflight_repos.py").read_text(encoding="utf-8")

    assert "usable[:limit]" in sweep_src, "the sweep no longer truncates its list"
    assert "present[:limit]" in pf_src, "the preflight no longer truncates its list"
    # The sweep sorts explicitly; the preflight does not. That asymmetry is exactly
    # why the agreement below is measured on data instead of inferred from code.
    assert "sorted(ds" in sweep_src, "the sweep no longer sorts by instance_id"
    assert "sorted(" not in pf_src.split("def load_instances")[1].split("def ")[0], (
        "the preflight now sorts too -- the asymmetry this test documents is gone, "
        "so the reasoning above needs revisiting"
    )

    # And measure it, so a dataset reordering is caught.
    ds = load_dataset("SWE-bench/SWE-bench_Lite", split="test")
    ordered = [r["instance_id"] for r in sorted(ds, key=lambda r: r["instance_id"])]
    natural = [r["instance_id"] for r in ds]
    assert ordered[:10] == natural[:10], (
        "the dataset is no longer in sorted order, so a count is not enough to "
        "identify the batch -- pass explicit ids to the preflight instead"
    )
