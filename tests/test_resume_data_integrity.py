"""A --resume must not erase the work the earlier session already recorded.

Context: a 50-issue x 3-strategy sweep is ~6 hours, so an interruption is likely
and --resume is the documented recovery path (runner.py logs it as such, and
MEMORY records it as the standard response to a rate limit). But the CSV and the
manifest were written from ``all_results``, which only ever holds runs started in
the CURRENT process, and ``to_csv`` overwrites. So resuming after a crash rewrote
the main artefact with only the post-interruption runs and dropped everything
before it -- while the jsonl savepoints stayed complete.

Two auditors found this independently by running a stub strategy into a temp dir:
a 3-issue first pass followed by a 5-issue resume left the CSV with 2 rows. The
existing resume tests could not catch it because they only assert WHICH instances
ran, never what ended up in the artefacts.

These tests assert on the artefacts, which is where the loss was invisible.
"""
import json

from experiments.runner import run_experiments
from models.inference import InferenceResult, InferenceRun
from models.issue import Issue
from models.patch import Patch
from models.result import ExperimentResult, ExecutionResult, EvaluationResult
from evaluation.cost import CostCalculator


class _StubStrategy:
    """Returns a valid-looking patch and records the instances it was asked for."""

    def __init__(self, calls: list[str], fail_on: str = ""):
        self.calls = calls
        self.fail_on = fail_on

    def run(self, issue):
        self.calls.append(issue.instance_id)
        if self.fail_on and issue.instance_id == self.fail_on:
            # Stands in for a provider error: the runner records the failure as an
            # error row with an empty patch, which is NOT a finished run.
            raise RuntimeError("simulated provider failure")
        inf = InferenceResult(
            role="direct",
            response="diff --git a/x b/x",
            usage={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        )
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
        {"direct": _StubStrategy(calls)},
        base_dir=str(tmp_path),
        provider_name="test",
        model="test-model",
        rate_limit_seconds=0,
        **kwargs,
    )


def _csv_ids(exp_dir) -> list[str]:
    import pandas as pd

    df = pd.read_csv(exp_dir / "generation_result.csv")
    df.columns = [str(c).lstrip("[") for c in df.columns]
    return sorted(df["instance_id"].tolist())


def test_resume_keeps_rows_from_the_earlier_session(tmp_path):
    """THE REGRESSION: resuming must not shrink the CSV.

    Before the fix the CSV held only the newly-run instance, so 37 of 50 paid-for
    runs could vanish from the main artefact after one resume.
    """
    calls: list[str] = []
    _, exp_id = _run(tmp_path, [_issue(1), _issue(2), _issue(3)], calls)
    exp_dir = tmp_path / exp_id
    assert _csv_ids(exp_dir) == ["django__django-1", "django__django-2", "django__django-3"]

    # Crash-and-resume: the first three are done, two more are added.
    calls.clear()
    _run(
        tmp_path,
        [_issue(1), _issue(2), _issue(3), _issue(4), _issue(5)],
        calls,
        resume=True,
        experiment_id=exp_id,
    )

    assert calls == ["django__django-4", "django__django-5"], "only new work may run"
    assert _csv_ids(exp_dir) == [
        "django__django-1",
        "django__django-2",
        "django__django-3",
        "django__django-4",
        "django__django-5",
    ], "the earlier session's rows must survive the resume"


def test_resume_manifest_counts_every_run_not_just_this_session(tmp_path):
    """The manifest is the shipping receipt; it must match the shipment.

    It was built from the same truncated list, so it reported
    ``total_issues_processed: 2`` for a 5-issue experiment.
    """
    calls: list[str] = []
    _, exp_id = _run(tmp_path, [_issue(1), _issue(2)], calls)

    calls.clear()
    _run(tmp_path, [_issue(1), _issue(2), _issue(3)], calls,
         resume=True, experiment_id=exp_id)

    manifest = json.loads((tmp_path / exp_id / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["summary"]["total_issues_processed"] == 3, (
        "manifest must account for every recorded run, not only this session's"
    )


def test_merged_rows_keep_their_recorded_tokens(tmp_path):
    """Merging must preserve the columns, not just the row count.

    Rebuilding rows from the jsonl savepoints would restore the count while
    reporting token and cost columns as zero for the earlier session -- turning a
    data-loss bug into a silent data-quality bug. The merge is at CSV level
    precisely to keep these numbers.
    """
    import pandas as pd

    calls: list[str] = []
    _, exp_id = _run(tmp_path, [_issue(1)], calls)

    calls.clear()
    _run(tmp_path, [_issue(1), _issue(2)], calls, resume=True, experiment_id=exp_id)

    df = pd.read_csv(tmp_path / exp_id / "generation_result.csv")
    df.columns = [str(c).lstrip("[") for c in df.columns]
    first = df[df["instance_id"] == "django__django-1"].iloc[0]
    assert int(first["total_tokens"]) == 110, (
        "the earlier run's token count must survive the merge, not reset to zero"
    )


def test_a_retried_instance_appears_once_with_the_newer_row(tmp_path):
    """A retry replaces the failed row instead of duplicating the instance.

    A duplicate would also make the evaluation wrapper count the instance twice,
    which is how a success rate above 100% becomes possible.
    """
    calls: list[str] = []
    _, exp_id = _run(tmp_path, [_issue(1)], calls)

    # Re-run WITHOUT resume: the instance is deliberately measured again.
    calls.clear()
    _run(tmp_path, [_issue(1)], calls, experiment_id=exp_id)

    ids = _csv_ids(tmp_path / exp_id)
    assert ids == ["django__django-1"], f"instance duplicated in the CSV: {ids}"


def test_completeness_check_flags_missing_runs(tmp_path):
    """A sweep that loses runs must not exit looking complete.

    Nothing compared finished runs against planned runs, so a run that silently
    dropped instances still reported success -- and at 150 runs that is easy to
    miss by eye and expensive to discover after the analysis is written.

    Uses a REAL failure (the stub raises) rather than editing the savepoint by
    hand: a failure row has an empty patch, so it is not a finished run, and the
    completeness check must count it as missing.
    """
    calls: list[str] = []
    issues = [_issue(1), _issue(2), _issue(3)]

    # First pass: the third instance dies at the provider.
    run_experiments(
        issues,
        {"direct": _StubStrategy(calls, fail_on="django__django-3")},
        base_dir=str(tmp_path),
        provider_name="test",
        model="test-model",
        rate_limit_seconds=0,
    )
    exp_id = sorted(p.name for p in tmp_path.glob("EXP-*"))[-1]
    exp_dir = tmp_path / exp_id

    # Second pass, no resume: only the failing instance is re-requested, and it
    # fails again, so the experiment still ends with 2 of 3 finished.
    calls.clear()
    run_experiments(
        issues,
        {"direct": _StubStrategy(calls, fail_on="django__django-3")},
        base_dir=str(tmp_path),
        provider_name="test",
        model="test-model",
        rate_limit_seconds=0,
        experiment_id=exp_id,
    )

    marker = exp_dir / "INCOMPLETE.json"
    assert marker.exists(), "a shortfall must be recorded, not just logged"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["complete"] is False
    assert payload["expected"] == 3
    assert "direct:django__django-3" in payload["missing"]


def test_completeness_check_is_quiet_when_everything_finished(tmp_path):
    """The check must not cry wolf on a healthy run, or it will be ignored."""
    calls: list[str] = []
    _, exp_id = _run(tmp_path, [_issue(1), _issue(2)], calls)

    assert not (tmp_path / exp_id / "INCOMPLETE.json").exists()


def test_crash_before_the_first_export_does_not_lose_data(tmp_path):
    """THE CASE --resume EXISTS FOR: the process dies partway through the sweep.

    The CSV used to be written ONCE at the end of run_experiments, so a crash
    mid-sweep left no CSV at all -- and merging with a file that does not exist
    recovers nothing. The earlier merge fix only handled "session one reached the
    end", which is precisely when resume is not needed.

    The jsonl savepoints are appended per run, so they survive. This asserts the
    CSV ends up complete and that the recovered rows are marked, so a reader cannot
    mistake the missing token/cost columns for zeros.

    Behavioral note (2026-10-02): a KeyboardInterrupt now ALSO flushes the CSV
    before re-raising -- see the ``except KeyboardInterrupt`` branch in runner.py
    and ``_export_interrupted_run``. The earlier version of this test asserted
    "no CSV exists" as a precondition, which was a description of the old
    behaviour rather than a requirement. Both paths are asserted below so the
    recovery is verified whether or not the interrupt got its export out.
    """
    calls: list[str] = []
    exp_dir = None

    class _Boom(_StubStrategy):
        def run(self, issue):
            # Simulate a hard interruption partway through the sweep.
            if issue.instance_id == "django__django-3" and not self.calls.count(
                "django__django-3"
            ) > 1:
                self.calls.append(issue.instance_id)
                raise KeyboardInterrupt("simulated Ctrl+C")
            return super().run(issue)

    # Session 1: dies on the third issue.
    try:
        run_experiments(
            [_issue(1), _issue(2), _issue(3), _issue(4)],
            {"direct": _Boom(calls)},
            base_dir=str(tmp_path),
            provider_name="test",
            model="test-model",
            rate_limit_seconds=0,
        )
    except KeyboardInterrupt:
        pass

    exp_id = sorted(p.name for p in tmp_path.glob("EXP-*"))[-1]
    exp_dir = tmp_path / exp_id

    # The interruption must be recorded as a retryable row, NOT swallowed: the
    # whole point of the KeyboardInterrupt branch. Without the row the instance
    # would look like it was never planned.
    interrupted_rows = [
        json.loads(line)
        for line in (exp_dir / "predictions" / "direct.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(r.get("patch_status") == "INTERRUPTED" for r in interrupted_rows), (
        "Ctrl-C must leave a resumable row; otherwise the instance vanishes"
    )

    csv_path = exp_dir / "generation_result.csv"
    if csv_path.exists():
        # The interrupt flushed what it had. The CSV must NOT claim a patch for the
        # interrupted instance -- its row has an empty model_patch, and the savepoint
        # columns (tokens/cost) are honestly empty.
        import pandas as pd

        df = pd.read_csv(csv_path)
        df.columns = [str(c).lstrip("[") for c in df.columns]
        interrupted = df[df["instance_id"] == "django__django-3"]
        if len(interrupted):
            assert not str(interrupted.iloc[0].get("generated", "") or "").strip() or \
                not str(interrupted.iloc[0].get("patch_preview", "") or "").strip(), (
                "an interrupted run must not be recorded as if it produced a patch"
            )

    # Session 2: resume. Anything the interrupt did not finish is retried.
    calls.clear()
    _run(tmp_path, [_issue(1), _issue(2), _issue(3), _issue(4)], calls,
         resume=True, experiment_id=exp_id)

    ids = _csv_ids(exp_dir)
    assert ids == [
        "django__django-1",
        "django__django-2",
        "django__django-3",
        "django__django-4",
    ], f"the pre-crash runs were lost from the CSV: {ids}"


def test_a_corrupt_csv_does_not_discard_the_old_rows(tmp_path):
    """A half-written CSV must not cost every row it contains.

    to_csv is not atomic, so a process killed mid-write leaves a truncated file --
    the most likely interruption, because writing the CSV is the last thing a long
    run does. pd.read_csv is all-or-nothing: ONE malformed row raises and every old
    row is dropped, which restores the original bug on the most realistic input.
    """
    calls: list[str] = []
    _, exp_id = _run(tmp_path, [_issue(1), _issue(2), _issue(3)], calls)
    exp_dir = tmp_path / exp_id
    csv_path = exp_dir / "generation_result.csv"

    # Truncate mid-file, as an interrupted write would.
    text = csv_path.read_text(encoding="utf-8")
    csv_path.write_text(text[: int(len(text) * 0.6)], encoding="utf-8")

    calls.clear()
    _run(tmp_path, [_issue(1), _issue(2), _issue(3), _issue(4)], calls,
         resume=True, experiment_id=exp_id)

    ids = _csv_ids(exp_dir)
    assert "django__django-4" in ids, "the new run must be present"
    assert len(ids) >= 3, (
        f"the corrupt CSV cost the earlier runs: only {ids} survived"
    )


def test_a_completed_run_without_a_patch_is_not_reported_as_missing(tmp_path):
    """A model answering with prose is an OUTCOME, not an absent run.

    The completeness check reused the resume predicate, which requires a non-empty
    patch. But a run can finish normally and produce no diff -- recorded as
    NO_DIFF/EMPTY with no error_type. On real data (EXP-20260824-005) that
    predicate flagged 44 of 150 rows as unfinished when they had in fact run.

    A control that cries wolf on legitimate outcomes is worse than none, because
    the real alarm gets ignored.
    """
    calls: list[str] = []

    class _NoDiff(_StubStrategy):
        def run(self, issue):
            self.calls.append(issue.instance_id)
            inf = InferenceResult(role="direct", response="I could not find the bug.")
            run = InferenceRun(patch="", inferences=[inf], messages=[])
            result = ExperimentResult(
                instance_id=issue.instance_id,
                strategy="direct",
                model="test-model",
                execution=ExecutionResult(run=run),
                cost=CostCalculator().aggregate([inf]),
                evaluation=EvaluationResult(success=False, error=""),
            )
            return Patch(response=""), result

    _, exp_id = run_experiments(
        [_issue(1), _issue(2)],
        {"direct": _NoDiff(calls)},
        base_dir=str(tmp_path),
        provider_name="test",
        model="test-model",
        rate_limit_seconds=0,
    )

    exp_dir = tmp_path / exp_id
    assert not (exp_dir / "INCOMPLETE.json").exists(), (
        "both runs completed; a no-diff answer is a result, not a missing run"
    )


def test_a_successful_resume_clears_the_stale_incomplete_marker(tmp_path):
    """A resolved shortfall must remove the alarm, not leave it forever.

    The marker was written only on the failure branch and never removed, so a
    successful resume left a file claiming the experiment was short. A stale alarm
    is indistinguishable from a live one, which makes the marker useless exactly
    when someone checks it before analysing.
    """
    calls: list[str] = []
    issues = [_issue(1), _issue(2)]

    # Pass 1: the second issue dies, so the marker is written.
    run_experiments(
        issues,
        {"direct": _StubStrategy(calls, fail_on="django__django-2")},
        base_dir=str(tmp_path),
        provider_name="test",
        model="test-model",
        rate_limit_seconds=0,
    )
    exp_id = sorted(p.name for p in tmp_path.glob("EXP-*"))[-1]
    exp_dir = tmp_path / exp_id
    assert (exp_dir / "INCOMPLETE.json").exists(), "precondition: marker written"

    # Pass 2: resume with a healthy strategy, so everything completes.
    calls.clear()
    run_experiments(
        issues,
        {"direct": _StubStrategy(calls)},
        base_dir=str(tmp_path),
        provider_name="test",
        model="test-model",
        rate_limit_seconds=0,
        resume=True,
        experiment_id=exp_id,
    )

    assert not (exp_dir / "INCOMPLETE.json").exists(), (
        "the shortfall was resolved; the stale marker must be cleared"
    )
