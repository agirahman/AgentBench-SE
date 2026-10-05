"""`--only N`: run about N still-INCOMPLETE runs (rounded up to whole issues).

Why this exists. A 150-run sweep is ~21 hours (thinking ON), so it gets run in stages: 30 runs,
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
  * `--only` ROUNDS UP to whole issues, so it is not a hard cap: `--only 4` with 3
    strategies selects 2 issues = 6 runs. The overshoot is at most
    len(strategies) - 1 runs, and --dry-run prints the exact count beforehand.
  * `runs_planned` records THIS session's runs; `runs_planned_cumulative` records
    the experiment total. Reporting only one of them misstates either the work
    about to happen or the experiment's size.
"""
import json

import pytest

from tests.test_sweep_preflight_scope import _load_sweep_module


# A real experiment directory name in the shape generate_experiment_id() produces
# (src/experiment_id.py:130: EXP-<YYYYMMDD>-<NNN>). The guard does NOT check a name
# pattern -- that was removed because it rejected real ids -- so this is just a
# realistic name; the directory is accepted because it has predictions/.
EXP_A = "EXP-20260101-001"
EXP_B = "EXP-20260101-002"


def _make_experiment(root, exp_id, strategies=()):
    """Create ``results/<exp_id>/predictions`` -- the shape a batch-1 run leaves.

    ``create_experiment_dir`` (src/experiment_id.py:133) always mkdirs the experiment
    directory, and the runner then always mkdirs ``predictions/`` (runner.py:990-991)
    before the first run. The guard accepts an experiment with EITHER ``predictions/``
    OR ``experiment.yaml``, so this helper builds the common (predictions/) shape; a
    directory with only ``experiment.yaml`` is also valid and is exercised by
    test_resume_accepts_an_experiment_with_only_experiment_yaml.
    """
    pred = root / "results" / exp_id / "predictions"
    pred.mkdir(parents=True, exist_ok=True)
    return pred


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
    exp = tmp_path / "results" / EXP_A
    _write_savepoint(exp, "direct", [_done("django__django-1000")])

    _run(mod, ["--only", "5", "--resume", "--exp-id", EXP_A,
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
    exp = tmp_path / "results" / EXP_A
    _write_savepoint(exp, "direct", [
        _done("django__django-1000"),
        {"instance_id": "django__django-1001", "strategy": "direct",
         "model_name_or_path": "cbai/deepseek-v4.1-flash", "thinking": True,
         "patch_status": "RATE_LIMIT", "model_patch": "", "error_type": "RateLimitError"},
    ])

    rc = _run(mod, ["--only", "1", "--resume", "--exp-id", EXP_A,
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
# --resume must point at a REAL experiment, not merely an existing path
# --------------------------------------------------------------------------

def test_resume_with_missing_exp_id_dir_is_an_error(sweep):
    """THE REGRESSION: --resume --exp-id <typo> used to succeed silently.

    Measured before the guard (read-only, --dry-run):

        --resume --exp-id EXP-TYPO-XXXX --only 3 --dry-run
        exit=0
        issues     : 1
        runs       : 3  (this session)
        experiment : 150 run(s) total; 147 already done

    The directory did not exist, yet it printed a plausible batch -- and the "147
    already done" came from the 50 candidate issues, not from any experiment. Without
    --dry-run the runner (runner.py:979-980) would CREATE results/EXP-TYPO-XXXX and
    run PAID jobs in it, so one typo spends real money on duplicate work that no
    analysis reads. Refuse with the same exit code (2) as its sibling guards.
    """
    mod, _, _ = sweep
    rc = _run(mod, ["--resume", "--exp-id", "EXP-20991231-999", "--only", "3",
                    "--dry-run"])
    assert rc == 2, "a resume target that does not exist must fail, not run"


def test_resume_error_names_the_missing_directory(sweep, capsys):
    """The message must show WHERE it looked, so a typo is visible at a glance."""
    mod, tmp_path, _ = sweep
    _run(mod, ["--resume", "--exp-id", "EXP-20991231-999", "--only", "3", "--dry-run"])
    err = capsys.readouterr().err
    assert "EXP-20991231-999" in err, f"the bad id must appear in the error: {err!r}"
    missing = str(tmp_path / "results" / "EXP-20991231-999")
    assert missing in err, (
        f"the directory that was searched for must be named: {err!r}"
    )


@pytest.mark.parametrize("bad_id", [
    "   ",              # whitespace only: the OS trims it, so results/ is "found"
    ".",                # resolves to results/ itself
    "..",               # resolves to ROOT (the whole project)
    "/",                # absolute path
    "C:\\Windows",      # absolute path (Windows drive)
    "\\\\server\\share",  # UNC path
    "csv",              # an EXISTING directory that is not an experiment
    "dry_run",          # ditto: made empty below, so it has no experiment marker
    "verify",           # ditto (a real results/verify has neither marker)
    "EXP-TYPO-XXXX",    # no such directory (the original typo case)
    "foo/bar",          # contains a path separator
    "foo\\bar",         # contains a path separator (Windows)
    "EXP-20260101-001/..",  # separator, and would escape the experiment dir
    " EXP-20260101-001",    # leading space
    "EXP-20260101-001 ",    # trailing space
    "EXP-\uff12\uff10\uff12\uff16\uff11\uff10\uff10\uff12-\uff12\uff17\uff19",  # fullwidth digits
])
def test_resume_rejects_anything_that_is_not_a_real_experiment(sweep, bad_id):
    """THE ROUND-2 LEAK: the round-1 guard asked "does this path exist".

    A reviewer measured that predicate leaking on every vector below -- each one
    exited 0 and LAUNCHED a paid sweep, because Path(results)/".." is ROOT, an
    absolute path discards the left side, and trailing spaces are trimmed by the OS.
    The predicate must be "is this a REAL experiment": a bare NAME that resolves
    strictly inside results/ and carries an experiment marker (predictions/ or
    experiment.yaml).

    "csv", "dry_run" and "verify" are names of directories that exist under results/ in
    this repo, so they are the sharpest cases: an existing directory that is still not
    an experiment. They are created EMPTY here (no marker), which is what makes them
    non-experiments -- a real results/dry_run DOES have predictions/ and is accepted
    (see test_resume_accepts_custom_named_experiments). The fullwidth-digit id is the
    round-3 T1 case: it is not a directory that exists, so the real-experiment
    predicate refuses it -- which a name-pattern regex did NOT (Python's `\\d` matches
    Unicode digits without re.ASCII).
    """
    mod, tmp_path, _ = sweep
    # Make the "existing but not an experiment" cases real on disk (no markers).
    for name in ("csv", "dry_run", "verify"):
        (tmp_path / "results" / name).mkdir(parents=True, exist_ok=True)

    rc = _run(mod, ["--resume", "--exp-id", bad_id, "--only", "1", "--dry-run"])
    assert rc == 2, f"a non-experiment --exp-id must be refused: {bad_id!r}"


def test_resume_rejects_an_existing_dir_without_any_marker(sweep):
    """A directory with NEITHER experiment marker is not a resumable experiment.

    An experiment carries predictions/ (runner.py:991) and/or experiment.yaml
    (runner.py:981-988). A directory with neither is something else on disk
    (results/csv, results/verify), not work this tool can continue.
    """
    mod, tmp_path, _ = sweep
    (tmp_path / "results" / EXP_A).mkdir(parents=True, exist_ok=True)  # no markers
    rc = _run(mod, ["--resume", "--exp-id", EXP_A, "--only", "1", "--dry-run"])
    assert rc == 2, "a directory with no experiment marker is not resumable"


def test_resume_accepts_an_experiment_with_only_experiment_yaml(sweep):
    """ROUND-4 F2: a run that died before predictions/ existed must still be resumable.

    runner.py order: create_experiment_dir (980) -> on_experiment_start writes
    experiment.yaml (981-988) -> patches/ (989) -> predictions/ (991). A run that dies
    between 981 and 991 has experiment.yaml but NO predictions/ -- it is a started
    experiment, and refusing it would strand real work. Accept EITHER marker.

    (Verified against the real results/ tree: no directory currently has
    experiment.yaml without predictions/, so this is a cheap guard against a state that
    is reachable but not yet present.)
    """
    mod, tmp_path, captured = sweep
    exp = tmp_path / "results" / EXP_A
    exp.mkdir(parents=True, exist_ok=True)
    (exp / "experiment.yaml").write_text("experiment_meta: {}\n", encoding="utf-8")
    # No predictions/ on purpose. NOT --dry-run, so build_cmd runs and the launch is
    # inspectable; subprocess.call is stubbed by the fixture, so nothing real runs.
    rc = _run(mod, ["--resume", "--exp-id", EXP_A, "--only", "1"])
    assert rc == 0, "an experiment with experiment.yaml but no predictions/ must resume"
    assert any("run_with_env" in str(a) for c in captured for a in c), (
        "the resume must actually launch"
    )


@pytest.mark.parametrize("base_name", ["EXP-20261002-279", "csv"])
def test_resume_ads_id_is_refused_without_a_traceback(sweep, capsys, base_name):
    """ROUND-4 F1: an NTFS Alternate Data Stream id must exit 2, not crash.

    Measured before the fix: `--resume --exp-id EXP-20261002-279::$DATA --only 1
    --dry-run` exited 1 with a raw traceback -- Path.is_dir() does not return False for
    a stream name, stat() raises PermissionError [WinError 5]. `name:stream` is a
    plausible NTFS typo, so it must be a clean, actionable refusal: exit 2 and NO
    traceback.

    The crash needs the BASE name to exist on disk (probed: `<dir>::$DATA`.is_dir()
    returns False when <dir> is absent, and raises only when <dir> exists), so this
    creates the base first -- otherwise the test would pass for the wrong reason.
    """
    mod, tmp_path, captured = sweep
    (tmp_path / "results" / base_name).mkdir(parents=True, exist_ok=True)
    bad_id = f"{base_name}::$DATA"
    rc = _run(mod, ["--resume", "--exp-id", bad_id, "--only", "1", "--dry-run"])
    assert rc == 2, f"an ADS id must be refused cleanly: {bad_id!r}"
    err = capsys.readouterr().err
    assert "Traceback" not in err, f"no traceback may escape: {err!r}"
    assert not any("run_with_env" in str(a) for c in captured for a in c), (
        "a refused id must not launch anything"
    )


def test_resume_accepts_a_high_counter_experiment(sweep):
    """ROUND-3 T2: the tool's OWN ids with counter >= 1000 must be resumable.

    ``generate_experiment_id`` returns f"EXP-{today}-{counter:03d}" -- ``:03d`` is a
    MINIMUM width, so the counter passes 999 into 4+ digits. The round-2 name-pattern
    regex ``EXP-\\d{8}-\\d{3}`` rejected those, i.e. the tool could not resume a
    directory it created itself. Reachable, not hypothetical: the counter increments
    once per main.py INVOCATION (not per run -- a 150-run sweep is ONE id), and
    results/experiment_index.json already shows a daily counter of 815 (20261001), so
    >999 is only ~185 more invocations away on a busy day. The real-experiment
    predicate has no name shape, so this must work.
    """
    mod, tmp_path, captured = sweep
    high = "EXP-20261002-1000"          # exactly the generator's output at counter=1000
    _write_savepoint(tmp_path / "results" / high, "direct", [
        {"instance_id": "django__django-1000", "strategy": "direct",
         "model_name_or_path": "cbai/deepseek-v4.1-flash", "thinking": True,
         "patch_status": "RATE_LIMIT", "model_patch": "", "error_type": "RateLimitError"},
    ])
    rc = _run(mod, ["--resume", "--exp-id", high, "--only", "1",
                    "--strategies", "direct"])
    assert rc == 0, f"{high} is a real experiment and must be resumable"
    assert _ids_in(_sweep_cmd(captured)) == ["django__django-1000"]


def test_resume_accepts_custom_named_experiments(sweep):
    """ROUND-3 T3: custom names the tool created on purpose must be resumable.

    results/dry_run, results/dry_run_groq and results/testing_run are real directories
    WITH predictions/, made by this tool, and src/main.py:76-78 documents `--exp-id
    <name>` as a valid target. The round-2 regex rejected them for not being "EXP-*".
    """
    mod, tmp_path, captured = sweep
    for name in ("dry_run", "testing_run"):
        _write_savepoint(tmp_path / "results" / name, "direct", [
            {"instance_id": "django__django-1000", "strategy": "direct",
             "model_name_or_path": "cbai/deepseek-v4.1-flash", "thinking": True,
             "patch_status": "RATE_LIMIT", "model_patch": "",
             "error_type": "RateLimitError"},
        ])
        rc = _run(mod, ["--resume", "--exp-id", name, "--only", "1",
                        "--strategies", "direct"])
        assert rc == 0, f"custom experiment {name!r} must be resumable"
        assert _ids_in(_sweep_cmd(captured)) == ["django__django-1000"]


def test_resume_rejects_a_symlink_that_escapes_results(sweep, tmp_path):
    """ROUND-3 T5: the is_relative_to branch is the ONLY thing blocking an escaping symlink.

    A symlink inside results/ whose target is outside results/ has name "evil" and no
    separator, so only the resolve-inside-results/ check stops it. Reviewer disabled
    that branch and the whole suite stayed green -- no test guarded it. This one does:
    if the branch is removed, the link resolves outside results/ yet points at a real
    directory WITH predictions/, so the guard would accept it and run a paid sweep into
    the target.

    On Windows os.symlink needs developer mode or admin; if that is unavailable the
    test SKIPS with the reason (never passes silently).
    """
    import os

    mod, _, captured = sweep
    # A real experiment OUTSIDE results/ -- this is what the link points at, and it
    # has predictions/ so ONLY the resolve-inside check can reject it.
    outside = tmp_path / "outside_exp"
    (outside / "predictions").mkdir(parents=True, exist_ok=True)
    results = tmp_path / "results"
    results.mkdir(parents=True, exist_ok=True)
    link = results / "evil"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink not permitted here: {type(exc).__name__}: {exc}")

    rc = _run(mod, ["--resume", "--exp-id", "evil", "--only", "1", "--dry-run"])
    assert rc == 2, "a symlink escaping results/ must be refused, not run"
    assert not any("run_with_env" in str(a) for c in captured for a in c), (
        "nothing may launch for a target that resolves outside results/"
    )


def test_resume_symlink_loop_is_refused_without_a_traceback(sweep, capsys, tmp_path):
    """ROUND-5 G1: a symlink LOOP must exit 2, not crash with a traceback.

    Path.resolve() raises RuntimeError ("Symlink loop from ...") -- NOT an OSError --
    so a guard whose try only caught OSError (the round-4 F1 fix, which wrapped is_dir()
    but left resolve() outside) still died with a raw traceback at the resolve() line.
    Measured with results/_loopA <-> _loopB: rc=1, traceback. The whole guard body is
    now inside one try/except (OSError, RuntimeError), so this must be a clean exit 2.

    On Windows os.symlink needs developer mode or admin; if that is unavailable the
    test SKIPS with the reason (never passes silently).
    """
    import os

    mod, _, captured = sweep
    results = tmp_path / "results"
    results.mkdir(parents=True, exist_ok=True)
    a = results / "_loopA"
    b = results / "_loopB"
    try:
        os.symlink("_loopB", a, target_is_directory=True)
        os.symlink("_loopA", b, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink not permitted here: {type(exc).__name__}: {exc}")

    rc = _run(mod, ["--resume", "--exp-id", "_loopA", "--only", "1", "--dry-run"])
    assert rc == 2, "a symlink loop must be refused, not crash"
    err = capsys.readouterr().err
    assert "Traceback" not in err, f"no traceback may escape: {err!r}"
    assert not any("run_with_env" in str(a) for c in captured for a in c), (
        "nothing may launch for a target that cannot be inspected"
    )


def test_exp_id_without_resume_is_dropped_by_build_cmd(sweep):
    """ROUND-3 T4: `--exp-id` WITHOUT `--resume` is DISCARDED, not honoured.

    An earlier comment claimed this was the documented "re-run into that directory"
    path. Measured, that path does not exist in this tool: build_cmd appends --exp-id
    only inside `if resume:`, so without --resume the id never reaches main.py. The
    old test asserted exit 0 and called that "the re-run path works" -- it passed for
    the wrong reason (the flag was thrown away). This test pins the real behaviour:
    exit 0 AND the built command does NOT contain --exp-id.

    (Do not "fix" build_cmd to forward it: a bare --exp-id would re-run everything INTO
    an existing experiment and overwrite it, which this staged-sweep wrapper does not
    want.)
    """
    mod, _, captured = sweep
    # NOT --dry-run: a dry run returns before build_cmd is executed, so there would be
    # no command to inspect. subprocess.call is stubbed by the fixture, so this still
    # executes nothing real.
    rc = _run(mod, ["--exp-id", "EXP-20991231-999", "--issues", "django__django-10914"])
    assert rc == 0
    cmd = _sweep_cmd(captured)
    assert "--exp-id" not in cmd, (
        f"--exp-id must NOT reach the runner without --resume, but it did: {cmd}"
    )


def test_resume_with_existing_exp_id_dir_still_works(sweep):
    """A real resume target must be accepted and launch the sweep.

    The fixture monkeypatches mod.ROOT to tmp_path, so results/<id> resolves inside
    it. One savepoint line records the issue; it is rate-limited, so it is still
    outstanding and the resume has something to do.
    """
    mod, tmp_path, captured = sweep
    exp = tmp_path / "results" / EXP_A
    _write_savepoint(exp, "direct", [
        {"instance_id": "django__django-1000", "strategy": "direct",
         "model_name_or_path": "cbai/deepseek-v4.1-flash", "thinking": True,
         "patch_status": "RATE_LIMIT", "model_patch": "", "error_type": "RateLimitError"},
    ])

    rc = _run(mod, ["--resume", "--exp-id", EXP_A, "--only", "1",
                    "--strategies", "direct"])
    assert rc == 0, "resuming an existing experiment must work"
    cmd = _sweep_cmd(captured)
    assert "--resume" in cmd and cmd[cmd.index("--exp-id") + 1] == EXP_A, (
        f"the resume flags must reach the runner: {cmd}"
    )


# --------------------------------------------------------------------------
# --limit must be a positive count
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad_limit", ["-1", "-5", "0"])
def test_negative_or_zero_limit_is_an_error(sweep, bad_limit):
    """THE SECOND ROUND-2 LEAK: --limit -1 planned 147 paid runs.

    Measured (read-only, --dry-run): `--limit -1` planned 147 runs and `--limit -5`
    planned 135, both exit 0 -- `usable[:limit]` with a negative index means "all but
    the last |limit|". One stray minus sign would have launched the whole sweep for
    real money. (Planned, not executed: the evidence is --dry-run, which is read-only.)
    `--limit 0` is refused too: `usable[:0]` runs nothing, a no-op that still looks
    like a launch.
    """
    mod, _, captured = sweep
    rc = _run(mod, ["--limit", bad_limit, "--dry-run"])
    assert rc == 2, f"--limit {bad_limit} must be refused"
    assert not any("run_with_env" in str(a) for c in captured for a in c), (
        "a rejected --limit must not launch anything"
    )


# --------------------------------------------------------------------------
# --only + --issues: context-dependent, NOT an error (round-3 T6)
# --------------------------------------------------------------------------

def test_only_with_issues_on_a_fresh_experiment_uses_the_issues(sweep, capsys):
    """A fresh experiment: --issues IS the candidate pool, so it is used silently.

    select_batch_for_only does `candidates = args.issues or select_issues(None)`, so
    with no savepoints yet, `--only 3 --issues X Y Z` means "work 3 runs from this
    set". Refusing the combination (an earlier round) rejected this legitimate use.
    """
    mod, tmp_path, captured = sweep
    _make_experiment(tmp_path, EXP_A)          # predictions/ exists, no savepoints
    rc = _run(mod, ["--resume", "--exp-id", EXP_A, "--only", "3",
                    "--issues", "django__django-7000", "django__django-7001",
                    "--strategies", "direct"])
    assert rc == 0, "a fresh experiment with --only + --issues must run"
    err = capsys.readouterr().err
    assert "WARNING" not in err, (
        f"--issues is USED on a fresh experiment, so no warning is warranted: {err!r}"
    )
    ids = _ids_in(_sweep_cmd(captured))
    assert ids == ["django__django-7000", "django__django-7001"], (
        f"the explicit --issues list must be the batch source, got {ids}"
    )


def test_only_with_issues_on_an_existing_experiment_warns(sweep, capsys):
    """An existing experiment: the batch comes from its savepoints, so --issues is inert.

    Measured on EXP-20261002-279: `--only 3 --issues django__django-9999` ran the
    experiment's OWN outstanding issues, not 9999. That is not an error -- it is the
    documented scope of --only -- but the user must be told the id list has no effect.
    """
    mod, tmp_path, captured = sweep
    _write_savepoint(tmp_path / "results" / EXP_A, "direct", [
        {"instance_id": "django__django-1000", "strategy": "direct",
         "model_name_or_path": "cbai/deepseek-v4.1-flash", "thinking": True,
         "patch_status": "RATE_LIMIT", "model_patch": "", "error_type": "RateLimitError"},
    ])
    # NOT --dry-run, so build_cmd runs and the batch is inspectable; subprocess.call is
    # stubbed by the fixture, so nothing real launches.
    rc = _run(mod, ["--resume", "--exp-id", EXP_A, "--only", "3",
                    "--issues", "django__django-9999"])
    assert rc == 0, "--only + --issues must run, not be refused"
    err = capsys.readouterr().err
    assert "WARNING" in err and "--issues" in err, (
        f"the inert --issues flag must be warned about clearly: {err!r}"
    )
    # And the batch must be the experiment's own issue, NOT the named 9999.
    ids = _ids_in(_sweep_cmd(captured))
    assert "django__django-9999" not in ids, (
        f"--issues must not leak into the batch of an existing experiment: {ids}"
    )
    assert ids == ["django__django-1000"], f"expected the experiment's own issue: {ids}"


# --------------------------------------------------------------------------
# Reading savepoints is best-effort: a corrupt jsonl must not crash (round-6 H1)
# --------------------------------------------------------------------------
# Both `recorded_ids` (inside select_batch_for_only) and `_experiment_has_savepoints`
# open predictions/<strategy>.jsonl. Before the guard, three shapes made main() die
# with a raw traceback (rc=1) instead of deciding a batch:
#   * the jsonl path is a DIRECTORY          -> PermissionError [WinError 5]
#   * the jsonl has invalid UTF-8            -> UnicodeDecodeError (raised on READ,
#                                               i.e. while iterating the handle)
#   * a line is valid JSON but not an object -> AttributeError from entry.get
# The same shapes also reach `_load_existing_ids` (runner.py:373/382) through the
# call site in select_batch_for_only, so one test below drives that path too.

def _assert_no_crash(rc, err):
    assert rc in (0, 2), f"a corrupt savepoint must not crash; got rc={rc}"
    assert "Traceback" not in err, f"no traceback may escape: {err!r}"


def test_jsonl_as_a_directory_does_not_crash(sweep, capsys):
    """`predictions/direct.jsonl` as a DIRECTORY: open() raises PermissionError."""
    mod, tmp_path, captured = sweep
    exp = tmp_path / "results" / EXP_A
    (exp / "predictions" / "direct.jsonl").mkdir(parents=True, exist_ok=True)

    rc = _run(mod, ["--resume", "--exp-id", EXP_A, "--only", "1", "--dry-run"])
    _assert_no_crash(rc, capsys.readouterr().err)


def test_jsonl_with_invalid_utf8_does_not_crash(sweep, capsys):
    """Invalid UTF-8 in the jsonl raises UnicodeDecodeError WHILE READING, not at open."""
    mod, tmp_path, captured = sweep
    exp = tmp_path / "results" / EXP_A
    (exp / "predictions").mkdir(parents=True, exist_ok=True)
    (exp / "predictions" / "direct.jsonl").write_bytes(
        b'{"instance_id": "django__django-1000"}\n\xff\xfe broken\n')

    rc = _run(mod, ["--resume", "--exp-id", EXP_A, "--only", "1", "--dry-run"])
    _assert_no_crash(rc, capsys.readouterr().err)


def test_jsonl_with_a_non_object_line_does_not_crash(sweep, capsys):
    """A valid-JSON-but-non-object line (`[1,2,3]` / `"s"`) made entry.get raise."""
    mod, tmp_path, captured = sweep
    exp = tmp_path / "results" / EXP_A
    (exp / "predictions").mkdir(parents=True, exist_ok=True)
    (exp / "predictions" / "direct.jsonl").write_text(
        '[1, 2, 3]\n"a bare string"\n', encoding="utf-8")

    rc = _run(mod, ["--resume", "--exp-id", EXP_A, "--only", "1", "--dry-run"])
    _assert_no_crash(rc, capsys.readouterr().err)


def test_corrupt_jsonl_reaches_the_issues_warning_check(sweep, capsys):
    """`--only --issues` calls _experiment_has_savepoints on the SAME file; same guard."""
    mod, tmp_path, captured = sweep
    exp = tmp_path / "results" / EXP_A
    (exp / "predictions" / "direct.jsonl").mkdir(parents=True, exist_ok=True)

    rc = _run(mod, ["--resume", "--exp-id", EXP_A, "--only", "1",
                    "--issues", "django__django-1000", "--dry-run"])
    _assert_no_crash(rc, capsys.readouterr().err)


def test_corrupt_jsonl_on_the_load_existing_ids_path_does_not_crash(sweep, capsys):
    """A READABLE savepoint makes select_batch_for_only call _load_existing_ids for
    EVERY strategy -- so a corrupt jsonl in another strategy must be tolerated there
    too (that loader is in runner.py and is unguarded; the call site wraps it)."""
    mod, tmp_path, captured = sweep
    exp = tmp_path / "results" / EXP_A
    # direct.jsonl is readable and holds a savepoint -> recorded_ids() is non-empty
    # -> the _load_existing_ids branch runs; planning.jsonl is a DIRECTORY.
    _write_savepoint(exp, "direct", [
        {"instance_id": "django__django-1000", "strategy": "direct",
         "model_name_or_path": "cbai/deepseek-v4.1-flash", "thinking": True,
         "patch_status": "RATE_LIMIT", "model_patch": "", "error_type": "RateLimitError"},
    ])
    (exp / "predictions" / "planning.jsonl").mkdir()

    rc = _run(mod, ["--resume", "--exp-id", EXP_A, "--only", "1", "--dry-run"])
    _assert_no_crash(rc, capsys.readouterr().err)


def test_corrupt_bill_window_state_file_does_not_crash(sweep, capsys):
    """The bill-window file is read on a REAL (non-dry-run) resume, best-effort too.

    `logs/sweep_started.json` is read back to append the previous attempt. The old
    code caught only OSError, so invalid UTF-8 (UnicodeDecodeError) and a valid-JSON
    non-object (AttributeError from previous.get) both crashed the launch. Reached
    only when NOT --dry-run, hence the dry-run tests above do not cover it.
    """
    mod, tmp_path, captured = sweep
    _make_experiment(tmp_path, EXP_A)          # predictions/ so the guard accepts it
    state = tmp_path / "logs" / "sweep_started.json"
    state.parent.mkdir(parents=True, exist_ok=True)

    for corrupt in (b"\xff\xfe not utf-8", b"[1, 2, 3]"):
        state.write_bytes(corrupt)
        # subprocess.call is stubbed by the fixture, so nothing real launches.
        rc = _run(mod, ["--resume", "--exp-id", EXP_A, "--only", "1"])
        _assert_no_crash(rc, capsys.readouterr().err)


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
    exp = tmp_path / "results" / EXP_A
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

    rc = _run(mod, ["--only", "2", "--resume", "--exp-id", EXP_A,
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
    exp = tmp_path / "results" / EXP_A
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

    rc = _run(mod, ["--only", "3", "--resume", "--exp-id", EXP_A,
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
    exp = tmp_path / "results" / EXP_A
    _write_savepoint(exp, "direct", [
        {"instance_id": f"django__django-{1000+i}", "strategy": "direct",
         "model_name_or_path": "cbai/deepseek-v4.1-flash", "thinking": True,
         "patch_status": "RATE_LIMIT", "model_patch": "", "error_type": "RateLimitError"}
        for i in range(5)
    ])

    rc = _run(mod, ["--only", "99", "--resume", "--exp-id", EXP_A,
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
    exp = tmp_path / "results" / EXP_A
    _write_savepoint(exp, "direct", [
        _done(f"django__django-{1000+i}") for i in range(5)
    ])

    rc = _run(mod, ["--only", "3", "--resume", "--exp-id", EXP_A,
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
    exp = tmp_path / "results" / EXP_A
    _write_savepoint(exp, "direct", [
        _done("django__django-1000"),
        {"instance_id": "django__django-1001", "strategy": "direct",
         "model_name_or_path": "cbai/deepseek-v4.1-flash", "thinking": True,
         "patch_status": "RATE_LIMIT", "model_patch": "",
         "error_type": "RateLimitError"},
    ])

    rc = _run(mod, ["--only", "2", "--resume", "--exp-id", EXP_A,
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
    exp = tmp_path / "results" / EXP_A
    _write_savepoint(exp, "direct", [
        _done("django__django-1000"),
        *[{"instance_id": f"django__django-{1000+i}", "strategy": "direct",
           "model_name_or_path": "cbai/deepseek-v4.1-flash", "thinking": True,
           "patch_status": "RATE_LIMIT", "model_patch": "",
           "error_type": "RateLimitError"} for i in range(1, 5)],
    ])

    _run(mod, ["--only", "2", "--resume", "--exp-id", EXP_A,
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


def test_only_rounds_up_to_whole_issues(sweep, capsys):
    """`--only` counts RUNS but selects ISSUES, so it rounds UP -- it is not a cap.

    A brand-new experiment with the default 3 strategies: `--only 4` cannot run
    exactly 4, because one issue yields 3 runs. It takes 2 whole issues = 6 runs.

    This is deliberate (the runner takes issue ids, not (issue, strategy) pairs, so
    a partial issue could not be evaluated), and the overshoot is at most
    len(strategies) - 1 runs. What was WRONG was the help text saying "at most N
    runs" -- false by up to 2 runs. This test pins the real behaviour so the wording
    cannot drift back to a claim the code does not honour.

    "Brand-new experiment" means what a real batch-1 (`--limit`) run leaves on disk:
    the directory exists WITH a predictions/ folder (the runner mkdirs it before the
    first run, runner.py:990-991) but holds no savepoints yet. It is not "an empty
    directory named EXP-NEW" -- that shape never occurs, and the --resume guard now
    requires an experiment marker (predictions/ or experiment.yaml).
    """
    mod, tmp_path, captured = sweep
    _make_experiment(tmp_path, EXP_B)          # predictions/ exists, no savepoints yet
    rc = _run(mod, ["--only", "4", "--resume", "--exp-id", EXP_B])
    assert rc == 0
    ids = _ids_in(_sweep_cmd(captured))
    assert len(ids) == 2, (
        f"4 requested runs must round UP to 2 issues (6 runs), not {len(ids)} issues"
    )
    out = capsys.readouterr().out
    assert "runs       : 6" in out, (
        f"the overshoot must be visible BEFORE running, not discovered afterwards: {out}"
    )
    assert "issues     : 2" in out, f"issue count must be shown: {out}"


def test_only_help_does_not_claim_a_hard_cap():
    """The help text must not promise "at most N" -- the code cannot honour it.

    Kept as a test because the wrong wording survived review once already: a claim
    in --help is documentation that users act on, and this one was off by up to
    len(strategies) - 1 runs.
    """
    import contextlib
    import io
    import sys

    mod = _load_sweep_module()
    old = sys.argv
    sys.argv = ["run_final_sweep.py", "--help"]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            with contextlib.suppress(SystemExit):
                mod.main()
    finally:
        sys.argv = old

    text = buf.getvalue()
    assert text, "argparse --help produced no output; the test would pass vacuously"
    # argparse WRAPS help at terminal width, so a phrase can be split across lines
    # ("Rounded\nUP"). Collapse whitespace before matching, or this test fails for a
    # reason that has nothing to do with the wording it is guarding.
    flat = " ".join(text.split())
    assert "at most N runs" not in flat, (
        "the help claims a hard cap the code does not enforce; --only rounds UP"
    )
    assert "rounded up" in flat.lower(), (
        "the help must state that --only rounds up to whole issues"
    )
