"""`--only N`: run at most N runs that are still INCOMPLETE.

Why this exists. A 150-run sweep is ~14 hours, so it gets run in stages: 30 runs,
check the output, continue. `--limit` cannot express that, because it is measured
in ISSUES:

    --resume --exp-id X --limit 20

means "look at the first 20 issues", and if 33 of the 50 issues are already done
then almost nothing runs -- the skip logic removes them. The user asked for "20
runs of work", not "the first 20 issues", and there was no way to say that.

`--only N` counts RUNS REMAINING (after resume has skipped the finished ones), so
the batch size is what was asked for regardless of how far the sweep got.

Design decisions pinned here:
  * `--only` without `--resume` is an ERROR (exit 2). "Runs remaining" is undefined
    without an experiment directory to measure against, so silently running the
    first N issues would be a different meaning under the same flag.
  * `--only` may need MORE issues than N to yield N runs, because an issue whose
    direct run is done still contributes its planning and review runs.
  * `runs_planned` records THIS session's runs; `runs_planned_cumulative` records
    the experiment total. Reporting only one of them misstates either the work
    about to happen or the experiment's size.
"""
import json

import pytest

from tests.test_sweep_preflight_scope import _load_sweep_module


def _write_savepoint(exp_dir, strategy, rows):
    pred = exp_dir / "predictions"
    pred.mkdir(parents=True, exist_ok=True)
    with open(pred / f"{strategy}.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _done(iid, strategy="direct", model="cbai/deepseek-v4.1-flash", thinking=True):
    return {
        "instance_id": iid, "strategy": strategy, "model_name_or_path": model,
        "thinking": thinking, "patch_status": "VALID",
        "model_patch": "diff --git a/x b/x\n",
    }


@pytest.fixture
def sweep(tmp_path, monkeypatch):
    import subprocess

    mod = _load_sweep_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    captured: list[list[str]] = []

    def fake_call(cmd, **kw):
        captured.append(list(cmd))
        return 0

    monkeypatch.setattr(subprocess, "call", fake_call)
    monkeypatch.setattr(
        mod, "select_issues",
        lambda limit: [f"django__django-{1000+i}" for i in range(limit if limit else 5)],
    )
    return mod, tmp_path, captured


def _run(mod, argv):
    import sys

    old = sys.argv
    sys.argv = ["run_final_sweep.py", *argv]
    try:
        return mod.main()
    finally:
        sys.argv = old


def _sweep_cmd(captured):
    """The run_with_env call (not the preflight)."""
    for c in captured:
        if any("run_with_env" in str(a) for a in c):
            return c
    raise AssertionError(f"no sweep command captured: {captured}")


def _ids_in(cmd):
    i = cmd.index("--instance-ids")
    out = []
    for a in cmd[i + 1:]:
        if str(a).startswith("--"):
            break
        out.append(a)
    return out


# --------------------------------------------------------------------------
# Guard rails
# --------------------------------------------------------------------------

def test_only_never_pulls_in_issues_outside_the_experiment(sweep, capsys):
    """THE REGRESSION (measured, cost real money).

    `--only` must CONTINUE an experiment, not extend it. An earlier version chose
    from all 50 candidates, so running `--only 2` against a 1-issue pilot picked
    two unrelated issues (10924, 11001) and actually ran them, spending money and
    writing rows into the pilot's CSV.

    With only 1 issue in the experiment there is at most 1 issue to continue, so
    the batch can never contain an id that is not already recorded.
    """
    mod, tmp_path, captured = sweep
    exp = tmp_path / "results" / "EXP-X"
    _write_savepoint(exp, "direct", [_done("django__django-1000")])

    _run(mod, ["--only", "5", "--resume", "--exp-id", "EXP-X",
               "--strategies", "direct"])
    # Every run in the experiment is done, so NOTHING may run and no id may be
    # invented from the candidate pool.
    assert not any("run_with_env" in str(a) for c in captured for a in c), (
        f"an experiment with no outstanding runs must not launch anything: {captured}"
    )
    out = capsys.readouterr().out
    assert "django__django-1001" not in out, (
        "an issue outside the experiment was pulled in"
    )


def test_only_continues_the_next_issue_in_order(sweep):
    """'resume and do the next N' -- continuing, not re-selecting.

    Issues 1000 and 1001 are both recorded for `direct`; 1000 is done. `--only 1`
    must take the NEXT outstanding run, which belongs to 1001, and must not touch
    the finished 1000.
    """
    mod, tmp_path, captured = sweep
    exp = tmp_path / "results" / "EXP-X"
    _write_savepoint(exp, "direct", [
        _done("django__django-1000"),
        {"instance_id": "django__django-1001", "strategy": "direct",
         "model_name_or_path": "cbai/deepseek-v4.1-flash", "thinking": True,
         "patch_status": "RATE_LIMIT", "model_patch": "", "error_type": "RateLimitError"},
    ])

    rc = _run(mod, ["--only", "1", "--resume", "--exp-id", "EXP-X",
                    "--strategies", "direct"])
    assert rc == 0
    ids = _ids_in(_sweep_cmd(captured))
    assert ids == ["django__django-1001"], (
        f"expected to continue with the next outstanding issue, got {ids}"
    )


def test_only_without_resume_is_an_error(sweep):
    """'Runs remaining' is undefined without an experiment to measure."""
    mod, _, _ = sweep
    rc = _run(mod, ["--only", "5"])
    assert rc == 2, "an undefined request must fail loudly, not run something else"


def test_only_still_requires_exp_id_via_resume_rule(sweep):
    """--only + --resume without --exp-id must keep failing as it does today."""
    mod, _, _ = sweep
    assert _run(mod, ["--only", "5", "--resume"]) == 2


# --------------------------------------------------------------------------
# The core behaviour
# --------------------------------------------------------------------------

def test_only_runs_that_many_incomplete_runs(sweep):
    """THE POINT: N runs of work, measured after the skip.

    The experiment holds 3 issues; issue 1000 is done. `--only 2` must plan 2
    runs -- issues 1001 and 1002 -- not "the first 2 issues" (which would be 1
    skipped + 1 run).
    """
    mod, tmp_path, captured = sweep
    exp = tmp_path / "results" / "EXP-X"
    for iid in ("django__django-1000", "django__django-1001", "django__django-1002"):
        _write_savepoint(exp, "direct", [_done(iid)])
    # Re-write without 1000 being done, to leave 2 outstanding.
    _write_savepoint(exp, "direct", [
        _done("django__django-1000"),
        {"instance_id": "django__django-1001", "strategy": "direct",
         "model_name_or_path": "cbai/deepseek-v4.1-flash", "thinking": True,
         "patch_status": "RATE_LIMIT", "model_patch": "", "error_type": "RateLimitError"},
        {"instance_id": "django__django-1002", "strategy": "direct",
         "model_name_or_path": "cbai/deepseek-v4.1-flash", "thinking": True,
         "patch_status": "RATE_LIMIT", "model_patch": "", "error_type": "RateLimitError"},
    ])

    rc = _run(mod, ["--only", "2", "--resume", "--exp-id", "EXP-X",
                    "--strategies", "direct"])
    assert rc == 0
    ids = _ids_in(_sweep_cmd(captured))
    assert ids == ["django__django-1001", "django__django-1002"], (
        f"expected exactly 2 still-incomplete runs, got {ids}"
    )


def test_only_counts_runs_not_issues(sweep):
    """One issue can contribute MORE than one run.

    With 3 strategies and an issue whose `direct` is done, that issue still has 2
    outstanding runs -- so `--only 3` needs only 2 issues, not 3.
    """
    mod, tmp_path, captured = sweep
    exp = tmp_path / "results" / "EXP-X"
    _write_savepoint(exp, "direct", [_done("django__django-1000")])
    _write_savepoint(exp, "planning",
                     [_done("django__django-1000", strategy="planning")])
    # Record 1001 so the experiment covers it (scope is the recorded set).
    _write_savepoint(exp, "review",
                     [_done("django__django-1001", strategy="review"),
                      {"instance_id": "django__django-1000", "strategy": "review",
                       "model_name_or_path": "cbai/deepseek-v4.1-flash",
                       "thinking": True, "patch_status": "RATE_LIMIT",
                       "model_patch": "", "error_type": "RateLimitError"}])

    rc = _run(mod, ["--only", "3", "--resume", "--exp-id", "EXP-X",
                    "--strategies", "direct", "planning", "review"])
    assert rc == 0
    ids = _ids_in(_sweep_cmd(captured))
    # 1000: review outstanding = 1 run. 1001: direct + planning = 2 runs. Total 3.
    assert ids == ["django__django-1000", "django__django-1001"], (
        f"expected 2 issues to yield 3 incomplete runs, got {ids}"
    )


def test_only_never_exceeds_the_available_runs(sweep):
    """Asking for more than remains must not invent issues or crash."""
    mod, tmp_path, captured = sweep
    exp = tmp_path / "results" / "EXP-X"
    _write_savepoint(exp, "direct", [
        {"instance_id": f"django__django-{1000+i}", "strategy": "direct",
         "model_name_or_path": "cbai/deepseek-v4.1-flash", "thinking": True,
         "patch_status": "RATE_LIMIT", "model_patch": "", "error_type": "RateLimitError"}
        for i in range(5)
    ])

    rc = _run(mod, ["--only", "99", "--resume", "--exp-id", "EXP-X",
                    "--strategies", "direct"])
    assert rc == 0
    ids = _ids_in(_sweep_cmd(captured))
    assert len(ids) == 5, f"only 5 runs exist in the experiment; got {ids}"
    assert all(i.startswith("django__django-100") for i in ids), (
        f"a run outside the experiment was included: {ids}"
    )


def test_only_returns_cleanly_when_nothing_remains(sweep, capsys):
    """A fully complete experiment must exit 0 with a clear message, running nothing."""
    mod, tmp_path, captured = sweep
    exp = tmp_path / "results" / "EXP-X"
    _write_savepoint(exp, "direct", [
        _done(f"django__django-{1000+i}") for i in range(5)
    ])

    rc = _run(mod, ["--only", "3", "--resume", "--exp-id", "EXP-X",
                    "--strategies", "direct"])
    assert rc == 0, "nothing to do is a success, not a failure"
    assert not any("run_with_env" in str(a) for c in captured for a in c), (
        "no sweep should be launched when nothing remains"
    )
    out = capsys.readouterr().out
    assert "nothing" in out.lower()


def test_failed_runs_still_count_as_remaining(sweep):
    """An infrastructure death must be retried, so it is NOT 'done'."""
    mod, tmp_path, captured = sweep
    exp = tmp_path / "results" / "EXP-X"
    _write_savepoint(exp, "direct", [
        _done("django__django-1000"),
        {"instance_id": "django__django-1001", "strategy": "direct",
         "model_name_or_path": "cbai/deepseek-v4.1-flash", "thinking": True,
         "patch_status": "RATE_LIMIT", "model_patch": "",
         "error_type": "RateLimitError"},
    ])

    rc = _run(mod, ["--only", "2", "--resume", "--exp-id", "EXP-X",
                    "--strategies", "direct"])
    assert rc == 0
    ids = _ids_in(_sweep_cmd(captured))
    assert ids == ["django__django-1001"], (
        f"the rate-limited run must be retried and the finished one skipped; got {ids}"
    )


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def test_session_and_cumulative_run_counts_are_both_reported(sweep, capsys):
    """One number cannot serve both purposes.

    The experiment records 5 issues x 1 strategy, one of them finished: this
    session runs 2, and the experiment holds 4 outstanding runs. Note "cumulative"
    counts the RECORDED instances, not the candidate pool -- an issue that was
    never part of this experiment is not its work.
    """
    mod, tmp_path, _ = sweep
    exp = tmp_path / "results" / "EXP-X"
    _write_savepoint(exp, "direct", [
        _done("django__django-1000"),
        *[{"instance_id": f"django__django-{1000+i}", "strategy": "direct",
           "model_name_or_path": "cbai/deepseek-v4.1-flash", "thinking": True,
           "patch_status": "RATE_LIMIT", "model_patch": "",
           "error_type": "RateLimitError"} for i in range(1, 5)],
    ])

    _run(mod, ["--only", "2", "--resume", "--exp-id", "EXP-X",
               "--strategies", "direct"])
    out = capsys.readouterr().out
    assert "2" in out, "this session's run count must be visible before starting"

    state = json.loads((tmp_path / "logs" / "sweep_started.json").read_text(encoding="utf-8"))
    assert state["runs_planned"] == 2, (
        f"runs_planned should be this session's work, got {state['runs_planned']}"
    )
    assert state.get("runs_planned_cumulative") == 4, (
        "the experiment's outstanding runs must also be recorded: "
        f"got {state.get('runs_planned_cumulative')}"
    )
