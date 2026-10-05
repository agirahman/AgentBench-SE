"""The configuration record must exist BEFORE the first run, not after the last.

Why this is worth a test: experiment.yaml used to be written by main.py only
after run_experiments() returned. A crash, a kill, or a lost process therefore
left an experiment directory full of patches and logs with no record of the
settings that produced them -- unreproducible by construction. The longer the
run, the worse the loss, and the budget-curve sweep runs for hours.

Discovered live: the level-40 sweep directory had no experiment.yaml while it
was running, and a progress watcher that identifies levels by reading
total_tool_turns from that file reported the wrong level's data.

Note on the strategy double: the runner catches strategy exceptions and records
them as error rows, so a failing strategy produces a normal return rather than
a raise. That is what makes it a usable probe -- the callback must have already
fired by the time the failure is logged.
"""
from experiments.runner import run_experiments
from models.issue import Issue


class _NoopStrategy:
    def __init__(self, name: str = "direct"):
        self.name = name

    def run(self, issue):
        raise RuntimeError("stop before doing any work")


def _issue() -> Issue:
    return Issue(
        instance_id="django__django-1",
        repo="django/django",
        base_commit="0" * 40,
        problem_statement="p",
    )


def _run(tmp_path, **kwargs):
    return run_experiments(
        [_issue()],
        {"direct": _NoopStrategy()},
        base_dir=str(tmp_path),
        provider_name="test",
        model="test-model",
        rate_limit_seconds=0,
        **kwargs,
    )


def test_callback_fires_before_any_run(tmp_path):
    """The record must be written while the experiment directory is still empty."""
    seen: list[tuple[str, str, int]] = []

    def on_start(exp_dir: str, exp_id: str) -> None:
        # Counting patches here proves the callback ran before the first run.
        patches = list((tmp_path / exp_id / "patches").glob("*"))
        seen.append((exp_dir, exp_id, len(patches)))

    _run(tmp_path, on_experiment_start=on_start)

    assert len(seen) == 1, "callback must fire exactly once"
    exp_dir, exp_id, patch_count = seen[0]
    # exp_dir arrives as a Path (create_experiment_dir returns one), so compare
    # against its string form rather than assuming a str.
    assert exp_id in str(exp_dir)
    assert patch_count == 0, "no patches may exist yet when the config is written"


def test_callback_failure_does_not_abort_the_experiment(tmp_path):
    """Losing a metadata write must not lose the whole run.

    The callback runs inside the runner before any work starts, so an exception
    escaping it would kill an experiment that is otherwise ready to go. Bad
    metadata is recoverable; a dead run is not. The run must still complete and
    still produce its result row.
    """
    def explode(exp_dir: str, exp_id: str) -> None:
        raise OSError("disk full")

    df, exp_id = _run(tmp_path, on_experiment_start=explode)

    assert exp_id, "the experiment must still have been created and run"
    assert len(df) == 1, "the run's result row must still be recorded"


def test_no_callback_is_fine(tmp_path):
    """Existing callers (tests, tooling) pass no callback and must keep working."""
    df, exp_id = _run(tmp_path)
    assert exp_id
    assert len(df) == 1
