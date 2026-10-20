"""Flag guards for the staged-sweep wrapper.

The 48-case flag audit found inputs that were accepted and then silently ignored.
Every guard here turns one of those into an exit 2 with a message naming the flag
that does what the caller probably meant. They all fire under ``--dry-run`` too:
a green preview must mean the command is runnable.

Measured before these guards (``--dry-run``, read-only):

    --exp-id X                     exit 0, the id was discarded by build_cmd
    --strategies nonsense          exit 0, 50 issues scheduled
    --strategies                   exit 0, ZERO runs scheduled
    --smoke --limit 5              exit 0, the limit was ignored
    --smoke --strategies review    exit 0, review was ignored
    --issues not__a-real-9999      exit 0, failed later during a paid run

A green dry run used to say nothing about whether the command would run, which is
the failure this file exists to stop.
"""
import pytest

from tests.test_sweep_preflight_scope import _load_sweep_module

EXP_A = "EXP-20260101-001"


@pytest.fixture
def sweep(tmp_path, monkeypatch):
    """main() with ROOT redirected and every subprocess stubbed.

    ``select_issues`` is stubbed for BOTH modes: the wrapper validates ``--issues``
    through the same function it uses to select a batch.
    """
    import subprocess

    mod = _load_sweep_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    captured: list[list[str]] = []
    monkeypatch.setattr(subprocess, "call",
                        lambda cmd, **kw: captured.append(list(cmd)) or 0)
    monkeypatch.setattr(
        mod, "select_issues",
        lambda limit=None, instance_ids=None: (
            list(instance_ids) if instance_ids is not None
            else [f"django__django-{1000+i}" for i in range(min(limit or 5, 5))]
        ),
    )
    (tmp_path / "results" / EXP_A / "predictions").mkdir(parents=True, exist_ok=True)
    return mod, tmp_path, captured


def _run(mod, argv):
    """Run main() and return its exit code, converting argparse's SystemExit."""
    import sys

    old = sys.argv
    sys.argv = ["run_final_sweep.py", *argv]
    try:
        return mod.main()
    except SystemExit as exc:
        return exc.code
    finally:
        sys.argv = old


def _assert_refused(rc, captured, flag):
    assert rc == 2, f"{flag} must be refused with exit 2, got {rc}"
    assert not captured, f"{flag} must not build or launch anything: {captured}"


# --------------------------------------------------------------------------
# --resume <=> --exp-id AND --only
# --------------------------------------------------------------------------

@pytest.mark.parametrize("argv,flag", [
    (["--exp-id", "EXP-20260101-001"], "--exp-id without --resume"),
    (["--resume", "--exp-id", "EXP-20260101-001"], "--resume without --only"),
    (["--resume", "--only", "3"], "--resume without --exp-id"),
    (["--exp-id", "EXP-20260101-001", "--only", "3"], "--exp-id without --resume"),
])
def test_resume_flag_triangle_is_enforced(sweep, argv, flag):
    """`--resume` <=> `--exp-id` AND `--only` -- each missing leg is refused."""
    mod, _, captured = sweep
    _assert_refused(_run(mod, argv), captured, flag)


@pytest.mark.parametrize("only", ["0", "-1", "-5"])
def test_only_must_be_positive(sweep, only):
    mod, _, captured = sweep
    _assert_refused(
        _run(mod, ["--resume", "--exp-id", EXP_A, "--only", only]),
        captured, f"--only {only}",
    )


def test_only_and_limit_cannot_both_bound_the_batch(sweep):
    """They bound different units (runs this session vs first N issues)."""
    mod, _, captured = sweep
    _assert_refused(
        _run(mod, ["--resume", "--exp-id", EXP_A, "--only", "3", "--limit", "2"]),
        captured, "--only + --limit",
    )


def test_allow_config_change_needs_resume(sweep):
    """The opt-out relaxes a RECORDED identity; without a resume there is none."""
    mod, _, captured = sweep
    _assert_refused(
        _run(mod, ["--allow-config-change"]), captured,
        "--allow-config-change without --resume",
    )


def _child_cmd(captured):
    return next(c for c in captured if any("run_with_env" in str(a) for a in c))


def test_allow_config_change_is_forwarded_to_the_child(sweep):
    """MAJOR review 4: the wrapper must be able to pass the opt-out on.

    ``--resume --exp-id`` is how the wrapper reaches main.py's identity guard, so
    if this flag never reaches the child, every legacy experiment (savepoints, no
    run_identity) is un-resumable from the wrapper -- the error message tells the
    operator to use a flag the wrapper does not have.
    """
    mod, _, captured = sweep
    assert _run(mod, ["--resume", "--exp-id", EXP_A, "--only", "1",
                      "--allow-config-change"]) == 0

    cmd = _child_cmd(captured)
    assert "--allow-config-change" in cmd, (
        f"the opt-out never reached main.py: {cmd}"
    )


def test_allow_config_change_is_absent_by_default(sweep):
    """Control: the opt-out must not be sent silently on every resume."""
    mod, _, captured = sweep
    assert _run(mod, ["--resume", "--exp-id", EXP_A, "--only", "1"]) == 0
    assert "--allow-config-change" not in _child_cmd(captured)


# --------------------------------------------------------------------------
# --strategies
# --------------------------------------------------------------------------

def test_unknown_strategy_is_refused_at_parse_time(sweep):
    """`--strategies nonsense` used to schedule a full 50-issue sweep."""
    mod, _, _ = sweep
    assert _run(mod, ["--strategies", "nonsense"]) == 2


def test_empty_strategies_is_refused(sweep):
    """`--strategies` with no value used to exit 0 having scheduled ZERO runs."""
    mod, _, _ = sweep
    assert _run(mod, ["--strategies"]) == 2


def test_strategies_defaults_to_all_three(sweep):
    """Omitting the flag is still the documented default."""
    mod, _, _ = sweep
    assert _run(mod, ["--limit", "1", "--dry-run"]) == 0
    assert _run(mod, ["--limit", "1"]) == 0
    assert "planning" in mod.DEFAULT_STRATEGIES


# --------------------------------------------------------------------------
# --smoke
# --------------------------------------------------------------------------

def test_smoke_plus_limit_is_refused(sweep):
    """The limit used to be discarded without a word."""
    mod, _, captured = sweep
    _assert_refused(_run(mod, ["--smoke", "--limit", "5"]), captured,
                    "--smoke + --limit")


def test_smoke_plus_strategies_is_refused(sweep):
    """--smoke already fixes the strategy; naming another used to be ignored."""
    mod, _, captured = sweep
    _assert_refused(_run(mod, ["--smoke", "--strategies", "review"]), captured,
                    "--smoke + --strategies")


def test_smoke_plus_only_is_refused(sweep, capsys):
    """--smoke fixes the batch to 1 issue, so a run cap alongside it is half-dead.

    Same class as --smoke + --limit: the strategies flip to direct while --only
    would change nothing observable, so the flag must be refused rather than
    silently ignored. ``--resume --exp-id --only`` is given so the OTHER --only
    guard (--only without --resume) cannot be what refuses this: without them the
    test would pass on the wrong guard.
    """
    mod, _, captured = sweep
    rc = _run(mod, ["--resume", "--exp-id", EXP_A, "--only", "3", "--smoke"])
    err = capsys.readouterr().err
    assert rc == 2, f"--smoke + --only must be refused, got {rc}"
    assert not captured, f"--smoke + --only must not launch anything: {captured}"
    assert "--smoke" in err and "drop --only" in err, (
        f"the message must name the --smoke/--only conflict, not another guard: {err!r}"
    )


def test_smoke_alone_runs_one_issue_one_strategy(sweep):
    mod, _, captured = sweep
    assert _run(mod, ["--smoke"]) == 0
    cmd = next(c for c in captured if any("run_with_env" in str(a) for a in c))
    i = cmd.index("--instance-ids")
    ids = []
    for a in cmd[i + 1:]:
        if str(a).startswith("--"):
            break
        ids.append(a)
    assert len(ids) == 1, f"--smoke must run one issue: {ids}"
    assert cmd[cmd.index("--strategies") + 1] == "direct"


# --------------------------------------------------------------------------
# --issues validation
# --------------------------------------------------------------------------

def test_unknown_issue_id_is_refused_and_named(sweep, capsys):
    """F20: the typo used to reach a real (paid) run before failing."""
    mod, _, captured = sweep
    mod.select_issues = lambda limit=None, instance_ids=None: (
        (_ for _ in ()).throw(ValueError(
            "unknown instance_id(s): not__a-real-9999. Pass ids exactly as they "
            "appear in SWE-bench Lite."))
        if instance_ids is not None else []
    )
    rc = _run(mod, ["--issues", "not__a-real-9999"])
    err = capsys.readouterr().err
    assert rc == 2, f"an unknown id must be refused, got {rc}"
    assert "not__a-real-9999" in err, f"the message must name the id: {err!r}"
    assert not captured, "a refused id must not launch anything"


def test_valid_issue_id_runs(sweep):
    mod, _, captured = sweep
    assert _run(mod, ["--issues", "django__django-10914"]) == 0
    assert any("run_with_env" in str(a) for c in captured for a in c)


def test_issues_without_a_value_is_refused(sweep):
    """A bare `--issues` parses to [] (falsy), so every later `or` fell through.

    Measured: the empty list was silently ignored and the selection fell through
    to select_issues(None) -- the WHOLE dataset, the F41 failure mode.
    """
    mod, _, captured = sweep
    _assert_refused(_run(mod, ["--issues"]), captured, "--issues with no value")


def test_offline_dataset_warns_instead_of_failing(sweep, capsys):
    """A dry run must not require the network, so a dataset failure only warns."""
    mod, _, _ = sweep
    mod.select_issues = lambda limit=None, instance_ids=None: (
        (_ for _ in ()).throw(OSError("no network")) if instance_ids is not None else []
    )
    rc = _run(mod, ["--issues", "django__django-10914", "--dry-run"])
    err = capsys.readouterr().err
    assert rc == 0, f"an offline validation must not block the preview, got {rc}"
    assert "WARNING" in err and "unverified" in err, (
        f"the skipped validation must be announced: {err!r}"
    )


# --------------------------------------------------------------------------
# --limit larger than the dataset
# --------------------------------------------------------------------------

def test_limit_above_the_dataset_warns_and_continues(sweep, capsys):
    """F05: clamping is fine, doing it silently is not."""
    mod, _, captured = sweep
    rc = _run(mod, ["--limit", "99", "--dry-run"])
    err = capsys.readouterr().err
    assert rc == 0, f"an oversized limit must still preview, got {rc}"
    assert "exceeds" in err and "99" in err, (
        f"the clamp must be announced: {err!r}"
    )


# --------------------------------------------------------------------------
# --only is a cap; --resume owns "is this run finished?"
# --------------------------------------------------------------------------

def test_select_batch_for_only_does_not_read_resume_state(sweep, monkeypatch):
    """Anti-duplication pin: the wrapper must not re-implement the predicate.

    ``select_batch_for_only`` used to call ``_load_existing_ids`` to compute
    outstanding runs per issue (F22: two owners of "is this run done?"). Making
    that loader explode proves the wrapper no longer consults it.
    """
    import experiments.runner as runner

    mod, tmp_path, _ = sweep
    (tmp_path / "results" / EXP_A / "predictions" / "direct.jsonl").write_text(
        '{"instance_id": "django__django-1000"}\n', encoding="utf-8")
    monkeypatch.setattr(
        runner, "_load_existing_ids",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError(
            "the wrapper must not re-implement the resume predicate")),
    )

    ids = mod.select_batch_for_only(3, EXP_A, ["direct"],
                                   ["django__django-1001"], model="m")
    assert ids == ["django__django-1000", "django__django-1001"]


def test_cap_continues_into_the_dataset_when_the_experiment_is_full(sweep):
    """F22 fix: a finished experiment must be able to start the next batch.

    The recorded ids come first, then the tail of the candidate pool. The cap is
    in runs, so `--only 10` with 1 strategy selects 10 issues.
    """
    mod, tmp_path, _ = sweep
    (tmp_path / "results" / EXP_A / "predictions" / "direct.jsonl").write_text(
        '{"instance_id": "django__django-1000"}\n', encoding="utf-8")

    ids = mod.select_batch_for_only(10, EXP_A, ["direct"],
                                    [f"django__django-{1000+i}" for i in range(20)],
                                    model="m")
    assert ids[0] == "django__django-1000", f"recorded ids come first: {ids}"
    assert len(ids) == 10, f"the cap must hold: {ids}"


def test_cap_rounds_up_to_whole_issues(sweep):
    mod, tmp_path, _ = sweep
    ids = mod.select_batch_for_only(4, EXP_A, ["direct", "planning", "review"],
                                    [f"i{i}" for i in range(10)], model="m")
    assert len(ids) == 2, f"4 runs / 3 strategies must round up to 2 issues: {ids}"


def test_cap_never_exceeds_the_candidates(sweep):
    mod, tmp_path, _ = sweep
    ids = mod.select_batch_for_only(99, EXP_A, ["direct"], ["a", "b"], model="m")
    assert ids == ["a", "b"]
