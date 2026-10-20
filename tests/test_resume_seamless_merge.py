"""Resuming into the same folder must MERGE, not shrink, every artefact.

`predictions/*.jsonl` is append-only and the CSV merge already keeps older rows, but
three artefacts were written from the CURRENT batch alone:

  * ``experiment.yaml`` -- ``dataset.n_issues`` / ``dataset.instance_ids``
  * ``manifest.json``   -- ``dataset.n_issues``
  * ``INCOMPLETE.json`` -- the completeness denominator

Measured consequence on a real experiment: EXP-20261012-005 recorded ``n_issues: 1``
while its CSV held 10 issues (30 runs). A reader of the YAML would conclude the
experiment was a 1-issue pilot.

These tests drive two sequential batches into one folder end to end -- the shape a
staged sweep produces -- and assert on the artefacts, because that is where the loss
was invisible. Nothing here calls a provider: every run uses the stub strategy.
"""
import json

import yaml

from experiments.runner import run_experiments
from main import _save_experiment_config
from models.inference import InferenceResult, InferenceRun
from models.issue import Issue
from models.patch import Patch
from models.result import EvaluationResult, ExecutionResult, ExperimentResult
from evaluation.cost import CostCalculator

STRATS = ["direct", "planning", "review"]


class _StubStrategy:
    """Returns a valid-looking patch and records which instances it ran."""

    def __init__(self, calls: list[str]):
        self.calls = calls

    def run(self, issue):
        self.calls.append(issue.instance_id)
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


def _issue(i: int) -> Issue:
    return Issue(
        instance_id=f"django__django-{i}",
        repo="django/django",
        base_commit="0" * 40,
        problem_statement="p",
    )


def _issue_in(repo_slug: str, i: int) -> Issue:
    """An issue from another repo, whose id re-derives to ``repo_slug``."""
    return Issue(
        instance_id=f"{repo_slug.replace('/', '__')}-{i}",
        repo=repo_slug,
        base_commit="0" * 40,
        problem_statement="p",
    )


def _batch(tmp_path, issues, calls, *, exp_id=None, resume=False):
    """Run one batch through the real runner, with main()'s config hook."""
    strategies = {name: _StubStrategy(calls) for name in STRATS}

    def write_config(exp_dir, written_id):
        _save_experiment_config(
            exp_dir,
            types_namespace(),
            issue_count=len(issues),
            strategy_names=STRATS,
            experiment_id=written_id,
            agents=[],
            repos={"django/django": len(issues)},
            instance_ids=[i.instance_id for i in issues] if resume else None,
        )

    return run_experiments(
        issues,
        strategies,
        base_dir=str(tmp_path),
        provider_name="test",
        model="test-model",
        rate_limit_seconds=0,
        resume=resume,
        experiment_id=exp_id,
        on_experiment_start=write_config,
    )


def types_namespace():
    """The minimal argparse namespace ``_save_experiment_config`` reads."""
    import types

    return types.SimpleNamespace(provider="deepseek", instance_ids=None)


def _csv_rows(exp_dir) -> list[str]:
    """Every (instance, strategy) row in the merged CSV, as ``id/strategy``."""
    import pandas as pd

    df = pd.read_csv(exp_dir / "generation_result.csv")
    df.columns = [str(c).lstrip("[") for c in df.columns]
    return sorted(f"{i}/{s}" for i, s in zip(df["instance_id"], df["strategy"]))


def _csv_ids(exp_dir) -> list[str]:
    import pandas as pd

    df = pd.read_csv(exp_dir / "generation_result.csv")
    df.columns = [str(c).lstrip("[") for c in df.columns]
    return sorted(set(df["instance_id"].tolist()))


def _jsonl_lines(exp_dir, strategy) -> int:
    path = exp_dir / "predictions" / f"{strategy}.jsonl"
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def test_two_batches_into_one_folder_merge_every_artefact(tmp_path):
    """THE CONTRACT: batch 2 continues batch 1 without shrinking anything."""
    calls: list[str] = []
    _, exp_id = _batch(tmp_path, [_issue(1), _issue(2)], calls)
    exp_dir = tmp_path / exp_id
    batch1_yaml = (exp_dir / "experiment.yaml").read_text(encoding="utf-8")
    batch1_csv = (exp_dir / "generation_result.csv").read_text(encoding="utf-8")

    calls.clear()
    _batch(tmp_path, [_issue(3), _issue(4)], calls, exp_id=exp_id, resume=True)

    # 1. CSV: 4 issues x 3 strategies = 12 rows, nothing dropped or duplicated.
    assert _csv_ids(exp_dir) == [
        "django__django-1", "django__django-2",
        "django__django-3", "django__django-4",
    ]
    assert len(_csv_rows(exp_dir)) == 12, (
        f"expected 4 issues x 3 strategies, got {_csv_rows(exp_dir)}"
    )
    assert list(dict.fromkeys(calls)) == ["django__django-3", "django__django-4"], (
        "only new work may run"
    )

    # 2. experiment.yaml describes the WHOLE experiment.
    cfg = yaml.safe_load((exp_dir / "experiment.yaml").read_text(encoding="utf-8"))
    assert cfg["dataset"]["n_issues"] == 4, (
        f"experiment.yaml shrank to the last batch: {cfg['dataset']}"
    )
    assert set(cfg["dataset"]["instance_ids"]) == {
        "django__django-1", "django__django-2",
        "django__django-3", "django__django-4",
    }

    # 3. manifest.json agrees with the CSV.
    manifest = json.loads((exp_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dataset"]["n_issues"] == 4, (
        f"manifest.dataset.n_issues is the batch size: {manifest['dataset']}"
    )

    # 4. Savepoints are append-only: 4 rows per strategy.
    for strategy in STRATS:
        assert _jsonl_lines(exp_dir, strategy) == 4, strategy

    # 5. Batch 1's YAML survived as a .bak, and batch 1's CSV rows are untouched.
    bak = exp_dir / "experiment.yaml.bak"
    assert bak.is_file(), "the pre-merge experiment.yaml was not preserved"
    bak_cfg = yaml.safe_load(bak.read_text(encoding="utf-8"))
    assert bak_cfg["dataset"]["n_issues"] == 2, (
        f"the .bak must hold batch 1's state, got {bak_cfg['dataset']}"
    )
    assert batch1_yaml != (exp_dir / "experiment.yaml").read_text(encoding="utf-8")

    assert batch1_csv != (exp_dir / "generation_result.csv").read_text(encoding="utf-8"), (
        "the CSV was expected to grow; an unchanged file means batch 2 was lost"
    )

    # 6. The merge is reconstructible without the terminal logs.
    log = (exp_dir / "MERGE_LOG.jsonl").read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in log if line.strip()]
    assert len(entries) == 2, f"one line per invocation, got {entries}"
    assert entries[1]["batch_ids"] == ["django__django-3", "django__django-4"]
    assert "django__django-3" in entries[1]["extended_with"]
    assert entries[1]["session_rc"] == 0


def test_completeness_uses_the_whole_experiment(tmp_path):
    """A small second batch must not trigger a false INCOMPLETE.json.

    Before the fix ``expected`` was this batch's size while ``completed`` counted the
    savepoints, so the check fired on every resume -- an alarm nobody reads.
    """
    calls: list[str] = []
    _, exp_id = _batch(tmp_path, [_issue(1), _issue(2)], calls)
    calls.clear()
    _batch(tmp_path, [_issue(3)], calls, exp_id=exp_id, resume=True)

    exp_dir = tmp_path / exp_id
    assert not (exp_dir / "INCOMPLETE.json").exists(), (
        "every run finished, so no shortfall may be reported"
    )


def test_a_corrupt_experiment_yaml_is_backed_up_and_replaced(tmp_path):
    """An unreadable YAML must not stop the sweep, and must not be lost either."""
    calls: list[str] = []
    _, exp_id = _batch(tmp_path, [_issue(1)], calls)
    exp_dir = tmp_path / exp_id
    raw = b"\xff\xfe not yaml at all"
    (exp_dir / "experiment.yaml").write_bytes(raw)

    calls.clear()
    _batch(tmp_path, [_issue(1), _issue(2)], calls, exp_id=exp_id, resume=True)

    bak = exp_dir / "experiment.yaml.bak"
    assert bak.is_file(), "the unreadable file was overwritten without a copy"
    assert bak.read_bytes() == raw, "the .bak must be the original bytes"
    cfg = yaml.safe_load((exp_dir / "experiment.yaml").read_text(encoding="utf-8"))
    assert cfg["dataset"]["n_issues"] == 2


def test_resume_repos_describe_the_merged_id_set(tmp_path):
    """``dataset.repos`` must cover every id, not just this batch's repo.

    The repos map used to be computed from the current batch, so a resume that
    added an issue from a SECOND repo still reported only the first repo -- next
    to a merged ``n_issues`` that counted both. Two different repos must produce
    two entries, matching ``dataset.instance_ids``.
    """
    calls: list[str] = []
    _, exp_id = _batch(tmp_path, [_issue(1)], calls)
    calls.clear()
    _batch(tmp_path, [_issue_in("psf/requests", 7)], calls, exp_id=exp_id, resume=True)

    cfg = yaml.safe_load((tmp_path / exp_id / "experiment.yaml").read_text(encoding="utf-8"))
    dataset = cfg["dataset"]
    assert set(dataset["instance_ids"]) == {"django__django-1", "psf__requests-7"}, dataset
    assert dataset["n_issues"] == 2, dataset
    assert dataset["repos"] == {"django/django": 1, "psf/requests": 1}, (
        f"repos must match instance_ids, got {dataset['repos']}"
    )
