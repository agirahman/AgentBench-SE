"""The sweep state file must record the bill window of EVERY attempt.

`sweep_started.json` holds the UTC windows used to reconcile our accounting
against the real 9router bill (`tools/read_actual_bill.py --compare`). A partner
audit (B2) found the write was unconditional, so a resume overwrote the window of
the attempt it was resuming -- money already spent could no longer be checked.

The fix made a resume APPEND, but it only folded in the attempt ALREADY in the
file. So the list always lagged exactly one behind: the run in progress entered
`attempts` only when a LATER resume folded it in.

Measured on a real 1-issue run + resume: the file held 1 attempt (the interrupted
first one) and the RESUME's own window was absent. For a sweep that completes with
no further resume, its window is never recorded at all -- and that is the normal
end state, not an edge case.

NOTE on clock resolution. Windows are second-resolution strings, and attempts are
deduplicated by `started_utc`. Two attempts that begin within the SAME second are
therefore indistinguishable and collapse into one entry -- correct behaviour for
this key, and why these tests advance the clock between attempts instead of
running twice back to back. A real sweep runs for hours, so its attempts never
collide; the tests below only need to model that separation.
"""
import json

import pytest

from tests.test_sweep_preflight_scope import _load_sweep_module


@pytest.fixture
def sweep_state(tmp_path, monkeypatch):
    """Run the sweep's state bookkeeping without a real sweep.

    subprocess.call is stubbed to succeed instantly; the module's ROOT is
    redirected so the real logs/sweep_started.json is never touched.
    """
    import subprocess

    mod = _load_sweep_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(subprocess, "call", lambda cmd, **kw: 0)
    monkeypatch.setattr(mod, "select_issues", lambda limit: ["inst-1"])
    monkeypatch.setattr(mod, "build_cmd", lambda *a, **k: ["python", "-c", "pass"])
    return mod, tmp_path / "logs" / "sweep_started.json"


def _run(mod, argv):
    import sys

    old = sys.argv
    sys.argv = ["run_final_sweep.py", *argv]
    try:
        return mod.main()
    finally:
        sys.argv = old


def _advance_clock(mod, monkeypatch, seconds):
    """Make the module's next `datetime.now()` read `seconds` later.

    A real sweep's attempts are hours apart; the tests must not depend on wall
    time passing, so the clock is shifted instead of sleeping.

    Patches ``mod.datetime`` -- the module does ``from datetime import datetime``,
    so it holds its OWN binding. Patching the ``datetime`` module would miss it
    entirely (MEMORY trap #19: a rebind on the module that holds the name).
    """
    import datetime as _dt

    real = mod.datetime
    delta = _dt.timedelta(seconds=seconds)

    class _Shifted(real):
        @classmethod
        def now(cls, tz=None):
            return real.now(tz) + delta

    monkeypatch.setattr(mod, "datetime", _Shifted)


def test_a_completed_sweep_records_its_window(sweep_state):
    """THE REGRESSION: the attempt that just ran must be in `attempts`.

    Before the fix only a LATER resume would add it, so a sweep that ended
    normally recorded no attempt at all.
    """
    mod, state_path = sweep_state
    _run(mod, ["--limit", "1"])

    state = json.loads(state_path.read_text(encoding="utf-8"))
    attempts = state.get("attempts") or []
    assert len(attempts) == 1, f"the completed attempt was not recorded: {attempts}"

    a = attempts[0]
    assert a["started_utc"] == state["started_utc"], (
        "the recorded attempt is not the one that ran"
    )
    assert a["finished_utc"] == state["finished_utc"]
    assert a["rc"] == 0
    assert a["elapsed_minutes"] is not None


def test_two_attempts_are_both_recorded(sweep_state, monkeypatch):
    """Run, then resume: BOTH windows must be present.

    This is the case the bill reconciliation depends on -- each attempt spends
    money, so each one's window must survive.
    """
    mod, state_path = sweep_state

    _run(mod, ["--limit", "1"])
    first = json.loads(state_path.read_text(encoding="utf-8"))
    first_started = first["started_utc"]

    _advance_clock(mod, monkeypatch, 3600)          # an hour later, like a real resume
    _run(mod, ["--limit", "1", "--resume", "--exp-id", "EXP-TEST-1"])
    state = json.loads(state_path.read_text(encoding="utf-8"))

    attempts = state.get("attempts") or []
    starts = [a["started_utc"] for a in attempts]
    assert first_started in starts, "the first attempt's window was lost on resume"
    assert len(attempts) == 2, (
        f"expected both attempts recorded, got {len(attempts)}: {attempts}"
    )
    assert len(set(starts)) == len(starts), f"duplicate attempt entries: {attempts}"
    assert attempts[-1]["resumed_from"] == "EXP-TEST-1", (
        "the resumed attempt must record what it continued"
    )


def test_the_attempt_is_not_recorded_twice_on_repeated_resume(sweep_state, monkeypatch):
    """Resuming twice must not duplicate the same attempt."""
    mod, state_path = sweep_state
    _run(mod, ["--limit", "1"])
    _advance_clock(mod, monkeypatch, 3600)
    _run(mod, ["--limit", "1", "--resume", "--exp-id", "EXP-TEST-1"])
    _advance_clock(mod, monkeypatch, 3600)
    _run(mod, ["--limit", "1", "--resume", "--exp-id", "EXP-TEST-1"])

    state = json.loads(state_path.read_text(encoding="utf-8"))
    starts = [a["started_utc"] for a in (state.get("attempts") or [])]
    assert len(starts) == len(set(starts)), f"duplicated windows: {starts}"
    assert len(starts) == 3, f"three attempts ran, {len(starts)} recorded: {starts}"


def test_attempts_in_the_same_second_collapse_to_one(sweep_state):
    """Document the dedupe boundary rather than leave it to be discovered.

    Windows are second-resolution, so two attempts starting in the same second
    share a key and become one entry. That is correct for the key -- and it is why
    a test that runs twice back to back must not expect two windows.
    """
    mod, state_path = sweep_state
    _run(mod, ["--limit", "1"])
    _run(mod, ["--limit", "1", "--resume", "--exp-id", "EXP-TEST-1"])

    state = json.loads(state_path.read_text(encoding="utf-8"))
    starts = [a["started_utc"] for a in (state.get("attempts") or [])]
    assert len(starts) == 1, (
        f"same-second attempts should collapse, got {starts}"
    )
