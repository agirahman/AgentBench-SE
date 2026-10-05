"""Resuming must CONTINUE the same experiment, not silently start a new one.

Why this matters for the actual constraint: there is no mid-run checkpoint, so
an interrupted run is lost. That makes long runs hostage to the machine staying
up -- which is the real problem when the only hardware is a laptop that cannot
run for a day straight. Per-run resume is what turns "one 22-hour session" into
"as many short sessions as needed".

Discovered while projecting a 50-issue run: run_experiments() calls
generate_experiment_id() unconditionally, so EVERY invocation creates a fresh
directory. The resume scan then reads predictions/ inside that new, empty
directory, so --resume could never skip anything. The flag looked like a
feature but was inert.

These tests pin the desired behaviour: given an experiment to continue, the
runner must write into THAT directory and skip the runs already recorded there.

Note the stub SUCCEEDS. A failing strategy also appends a savepoint row, and an
earlier version of these tests used one for convenience -- but a row written by
the error path has an empty patch, and an empty patch must be retried rather than
skipped (tests/test_resume_skips_failures.py). So only a successful run may be
treated as done, and the stub has to model that.
"""
from experiments.runner import run_experiments
from models.inference import InferenceResult, InferenceRun
from models.issue import Issue
from models.patch import Patch
from models.result import ExperimentResult, ExecutionResult, EvaluationResult
from evaluation.cost import CostCalculator


class _RecordingStrategy:
    """Records which instances it was asked to run, then returns a real patch."""

    def __init__(self, calls: list[str]):
        self.calls = calls

    def run(self, issue):
        self.calls.append(issue.instance_id)
        inf = InferenceResult(role="direct", response="diff --git a/x b/x")
        run = InferenceRun(
            patch="diff --git a/x b/x", inferences=[inf], messages=[]
        )
        result = ExperimentResult(
            instance_id=issue.instance_id,
            strategy="direct",
            model="test-model",
            execution=ExecutionResult(run=run),
            cost=CostCalculator().aggregate([inf]),
            evaluation=EvaluationResult(success=True, error=""),
        )
        return Patch(response="diff --git a/x b/x"), result


def _issue(i: int) -> Issue:
    return Issue(
        instance_id=f"django__django-{i}",
        repo="django/django",
        base_commit="0" * 40,
        problem_statement="p",
    )


def _run(tmp_path, issues, calls, **kwargs):
    return run_experiments(
        issues,
        {"direct": _RecordingStrategy(calls)},
        base_dir=str(tmp_path),
        provider_name="test",
        model="test-model",
        rate_limit_seconds=0,
        **kwargs,
    )


def test_resume_continues_the_named_experiment(tmp_path):
    """A resumed run must write into the SAME directory, not a new one."""
    issues = [_issue(1), _issue(2)]

    calls: list[str] = []
    _, exp_id = _run(tmp_path, issues, calls)
    assert calls == ["django__django-1", "django__django-2"]
    assert (tmp_path / exp_id / "predictions" / "direct.jsonl").exists()

    # Continue the same experiment, asking for both issues again. Both are
    # already recorded, so nothing should run.
    calls.clear()
    _, exp_id2 = _run(tmp_path, issues, calls, resume=True, experiment_id=exp_id)

    assert exp_id2 == exp_id, "resume must reuse the experiment, not fork a new one"
    assert calls == [], "both runs were already recorded and must be skipped"
    assert len(list(tmp_path.glob("EXP-*"))) == 1, "no second directory may appear"


def test_resume_runs_only_what_is_missing(tmp_path):
    """Half-done work must be topped up, not repeated."""
    calls: list[str] = []
    _, exp_id = _run(tmp_path, [_issue(1)], calls)

    # Same experiment, now asking for two issues. Only the second is missing.
    calls.clear()
    _, exp_id2 = _run(tmp_path, [_issue(1), _issue(2)], calls,
                      resume=True, experiment_id=exp_id)

    assert exp_id2 == exp_id
    assert calls == ["django__django-2"], "the completed run must not be repeated"


def test_without_resume_a_named_experiment_still_runs_everything(tmp_path):
    """Naming a directory is not itself permission to skip work.

    --exp-id selects WHERE to write; --resume decides whether to SKIP. Keeping
    them separate means a deliberate re-run of a finished experiment is possible
    (e.g. re-measuring after a code change) instead of being silently skipped.
    """
    calls: list[str] = []
    _, exp_id = _run(tmp_path, [_issue(1)], calls)

    calls.clear()
    _, exp_id2 = _run(tmp_path, [_issue(1)], calls, experiment_id=exp_id)

    assert exp_id2 == exp_id
    assert calls == ["django__django-1"], "without resume, the run must happen again"
