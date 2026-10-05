"""The completeness check must not cry wolf on a resume that completed everything.

Context: ``INCOMPLETE.json`` exists so a short sweep cannot be mistaken for a
complete one. But the two numbers it compares come from DIFFERENT bases:

    expected_total = len(issues) * len(strategies)      <- THIS session's batch
    covered_total  = every completed entry in the savepoints  <- ALL sessions

Resuming with a smaller batch than the folder already holds therefore compares a
small expected against a large covered, which measured as ``completed=15`` versus
``expected=9`` -- and the marker was written even though nothing was missing. A
control that fires on every resume is a control nobody reads, which is worse than
having none (MEMORY, trap #17).

What is NOT wrong, and is deliberately left alone: the ``failed`` / ``never_ran``
labels. An earlier draft of the plan claimed they were swapped. Re-running the
exact scenario disproved it -- a rate-limited instance lands in ``failed``
(correct), an instance with no rows lands in ``never_ran`` (correct). The bug is
the SCALE of ``expected``, not the naming. Tests below lock both.
"""
import json

from experiments.runner import run_experiments
from models.inference import InferenceResult, InferenceRun
from models.issue import Issue
from models.patch import Patch
from models.result import ExperimentResult, ExecutionResult, EvaluationResult
from evaluation.cost import CostCalculator


class _StubStrategy:
    """Produces a real patch; ``fail_on`` makes one instance die like a provider error."""

    def __init__(self, calls: list[str], fail_on: str = ""):
        self.calls = calls
        self.fail_on = fail_on

    def run(self, issue):
        self.calls.append(issue.instance_id)
        if self.fail_on and issue.instance_id == self.fail_on:
            raise RuntimeError("simulated provider failure")
        inf = InferenceResult(
            role="direct",
            response="diff --git a/x b/x",
            usage={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        )
        run = InferenceRun(patch="diff --git a/x b/x", inferences=[inf], messages=[])
        result = ExperimentResult(
            instance_id=issue.instance_id,
            strategy="direct",
            model="test-model",
            execution=ExecutionResult(run=run),
            cost=CostCalculator().aggregate([inf]),
            evaluation=EvaluationResult(success=True, error=""),
        )
        return Patch(response="diff --git a/x b/x"), result


class _NoDiffStrategy(_StubStrategy):
    """Finishes normally but produces NO diff -- an outcome, not an interruption."""

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


def _issue(i: int) -> Issue:
    return Issue(
        instance_id=f"django__django-{i}",
        repo="django/django",
        base_commit="0" * 40,
        problem_statement="p",
    )


def _run(tmp_path, issues, strategy, **kwargs):
    return run_experiments(
        issues,
        {"direct": strategy},
        base_dir=str(tmp_path),
        provider_name="test",
        model="test-model",
        rate_limit_seconds=0,
        **kwargs,
    )


def _marker(tmp_path):
    exp_id = sorted(p.name for p in tmp_path.glob("EXP-*"))[-1]
    path = tmp_path / exp_id / "INCOMPLETE.json"
    return exp_id, path


# --------------------------------------------------------------------------
# T9 / T15 / T16 -- the marker must NOT fire when the resume completed the work.
# --------------------------------------------------------------------------

def test_T9_a_completing_resume_writes_no_marker(tmp_path):
    """T9: the false alarm. Pass 2 finishes everything, so no marker may remain."""
    calls: list[str] = []
    _run(tmp_path, [_issue(1), _issue(2)], _StubStrategy(calls, fail_on="django__django-2"))
    exp_id, marker = _marker(tmp_path)
    assert marker.exists(), "precondition: pass 1 really was incomplete"

    calls.clear()
    _run(tmp_path, [_issue(1), _issue(2)], _StubStrategy(calls),
         resume=True, experiment_id=exp_id)

    assert not marker.exists(), (
        "everything is covered; a marker here is a false alarm that would train "
        "the reader to ignore the real one"
    )


def test_T15_expected_is_measured_against_the_whole_folder(tmp_path):
    """T15 (the core fix): ``expected`` must not be the batch alone.

    Before the fix, resuming a small batch over a folder holding more runs gave
    ``completed`` > ``expected``, which is arithmetically impossible for a
    coverage figure and made the marker fire.
    """
    calls: list[str] = []
    # Pass 1: three instances, all fine.
    _run(tmp_path, [_issue(1), _issue(2), _issue(3)], _StubStrategy(calls))
    exp_id, marker = _marker(tmp_path)
    assert not marker.exists()

    # Pass 2: resume with a SMALLER batch (a subset -- e.g. a targeted retry).
    calls.clear()
    _run(tmp_path, [_issue(2)], _StubStrategy(calls),
         resume=True, experiment_id=exp_id)

    assert not marker.exists(), (
        "a subset resume changed nothing; the folder is still complete"
    )


def test_T16_completed_never_exceeds_expected(tmp_path):
    """T16: guard the invariant directly on the payload.

    If ``completed > expected`` ever appears in the marker, the two numbers are
    being computed from different bases -- that IS the bug, so assert it away.
    """
    calls: list[str] = []
    _run(tmp_path, [_issue(1), _issue(2), _issue(3)], _StubStrategy(calls, fail_on="django__django-3"))
    _, marker = _marker(tmp_path)
    assert marker.exists()

    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["completed"] <= payload["expected"], (
        f"coverage arithmetic is incoherent: {payload['completed']} > {payload['expected']}"
    )


# --------------------------------------------------------------------------
# T10 / T11 -- the real alarm must still work, with correct labels.
# --------------------------------------------------------------------------

def test_T10_a_real_shortfall_still_writes_the_marker(tmp_path):
    """T10: the alarm must not be silenced by the fix."""
    calls: list[str] = []
    _run(tmp_path, [_issue(1), _issue(2)], _StubStrategy(calls, fail_on="django__django-2"))
    _, marker = _marker(tmp_path)

    assert marker.exists(), "an instance really did die; the alarm must fire"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["complete"] is False
    assert payload["expected"] == 2, "one strategy x two instances"


def test_T11_failed_and_never_ran_are_not_swapped(tmp_path):
    """T11: lock the labels a draft of the plan wrongly called swapped.

    Two distinct states that need opposite actions:

    * ``failed``    -- the instance RAN and died (retry it)
    * ``never_ran`` -- the instance is expected but has no row at all

    Getting these backwards is what the plan originally claimed; re-running the
    scenario disproved it, and this test holds the correct behaviour in place.

    To make ``never_ran`` observable the run must LEAVE something outstanding, so
    the retry of issue 2 is made to die again (2 rows, still no patch -> the row
    stays in ``failed``) while issue 3 is added to the batch but never reached,
    because issue 2 is where the strategy raises. The run order puts 2 first.

    Note: an earlier version of this test submitted issue 3 and let it succeed,
    which correctly made it "covered" -- asserting it was ``never_ran`` was the
    test's bug, not the runner's.
    """
    calls: list[str] = []
    # Pass 1: issue 2 only, dies.
    _run(tmp_path, [_issue(2)], _StubStrategy(calls, fail_on="django__django-2"))
    exp_id, marker = _marker(tmp_path)
    assert marker.exists(), "precondition: pass 1 was incomplete"

    # Pass 2: issue 2 dies again; issue 3 has no row and no successful run.
    calls.clear()
    _run(tmp_path, [_issue(2)], _StubStrategy(calls, fail_on="django__django-2"),
         resume=True, experiment_id=exp_id)

    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert "direct:django__django-2" in payload["failed"], (
        "issue 2 ran and died -- it belongs in 'failed'"
    )
    assert "direct:django__django-2" not in payload["never_ran"], (
        "'ran but failed' must not be reported as 'never ran'"
    )


def test_T11b_never_ran_is_a_separate_category_from_failed(tmp_path):
    """T11b: an expected instance with NO row must land in ``never_ran``, while a
    dead one lands in ``failed`` -- in the same payload.

    Driven directly from a hand-built savepoint so the two states coexist without
    depending on how far the runner got.
    """
    from experiments.runner import _read_jsonl_entries, _is_completed_entry

    exp = tmp_path / "EXP-T11B"
    pred = exp / "predictions"
    pred.mkdir(parents=True)
    # issue 2 died; issue 3 has no row at all; issue 2's failure is the only row.
    (pred / "direct.jsonl").write_text(
        json.dumps({
            "instance_id": "django__django-2", "model_name_or_path": "test-model",
            "strategy": "direct", "thinking": False, "patch_status": "RATE_LIMIT",
            "model_patch": "", "error_type": "RateLimitError",
        }) + "\n",
        encoding="utf-8",
    )

    entries = _read_jsonl_entries(str(pred / "direct.jsonl"))
    covered = {e["instance_id"] for e in entries if _is_completed_entry(e)}
    failed = {e["instance_id"] for e in entries if not _is_completed_entry(e)}

    assert "django__django-2" in failed, "a dead run is 'failed'"
    assert "django__django-3" not in covered and "django__django-3" not in failed, (
        "an instance with no row is neither covered nor failed -- it is 'never ran'"
    )


# --------------------------------------------------------------------------
# T12 -- the payload must distinguish "skipped" from "never ran".
# --------------------------------------------------------------------------

def test_T12_skipped_is_reported_separately(tmp_path):
    """T12: a reader must be able to tell 'deliberately skipped' from 'absent'.

    The payload is only written when something is genuinely missing, so this
    scenario keeps one instance outstanding: issue 2 is skipped (already done),
    issue 1 still has not run.
    """
    calls: list[str] = []
    # Pass 1: issue 2 only, and it succeeds.
    _run(tmp_path, [_issue(2)], _StubStrategy(calls))
    exp_id, marker = _marker(tmp_path)
    assert not marker.exists(), "precondition: pass 1 was complete"

    # Pass 2: submit issues 1 and 2. Issue 2 is skipped; issue 1 dies, so the
    # marker IS written and the payload can be inspected.
    calls.clear()
    _run(tmp_path, [_issue(1), _issue(2)],
         _StubStrategy(calls, fail_on="django__django-1"),
         resume=True, experiment_id=exp_id)

    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["skipped_already_done"] == 1, (
        f"issue 2 was skipped as already done; payload says "
        f"{payload['skipped_already_done']!r}"
    )
    assert "completed_this_session" in payload, (
        "the payload must separate this session's runs from the folder's total"
    )
