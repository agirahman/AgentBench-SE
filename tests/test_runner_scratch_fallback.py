"""The runner's raw-response fallback must still filter scratch files.

``capture_diff`` (agents/tools.py) filters agent scratch files from the
tool-calling path, but the runner has a SECOND path to a submitted patch: the
"Item 8a" fallback (runner.py), which ships the model's RAW text when
``extract_diff`` produced nothing but the response still contains ``diff --git``.
That raw text never passed through ``capture_diff``, so scratch files it carries
would reach the submitted patch -- the same class of leak as EXP-20261010-062,
where 17 throwaways shipped in one patch.

The fallback runs on a CLEAN git checkout (``capture_diff`` returns ""), which is
exactly the scenario these tests build: a tmp repo with one committed file and a
strategy whose Patch.response is a raw multi-file diff containing a scratch file.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from models.inference import InferenceResult, InferenceRun  # noqa: E402
from models.issue import Issue  # noqa: E402
from models.patch import Patch  # noqa: E402
from models.result import (  # noqa: E402
    CostSummary,
    EvaluationResult,
    ExecutionResult,
    ExperimentResult,
)

REAL_FILE = "django/forms/widgets.py"

#: Raw two-file diff. The scratch chunk carries a MALFORMED hunk header
#: ("@@xx"), which makes ``extract_diff`` reject the WHOLE response
#: (MALFORMED_HEADER, patch="") -- that is exactly what puts the runner on its
#: raw-response fallback. Dropping the scratch chunk leaves a valid single-file
#: patch, so this shape also proves the filter rescues the run.
RAW_RESPONSE = (
    "diff --git a/_exp6.py b/_exp6.py\n"
    "new file mode 100644\n"
    "index 0000000..e69de29\n"
    "--- /dev/null\n"
    "+++ b/_exp6.py\n"
    "@@xx bad header\n"
    "+print(1)\n"
    "diff --git a/django/forms/widgets.py b/django/forms/widgets.py\n"
    "index 8a1b2c3..9d4e5f6 100644\n"
    "--- a/django/forms/widgets.py\n"
    "+++ b/django/forms/widgets.py\n"
    "@@ -1 +1 @@\n"
    "-x = 1\n"
    "+x = 2\n"
)

#: The real file's chunk alone -- what the filter must leave behind, byte for
#: byte, and a patch that parses as VALID on its own.
CLEAN_PATCH = (
    "diff --git a/django/forms/widgets.py b/django/forms/widgets.py\n"
    "index 8a1b2c3..9d4e5f6 100644\n"
    "--- a/django/forms/widgets.py\n"
    "+++ b/django/forms/widgets.py\n"
    "@@ -1 +1 @@\n"
    "-x = 1\n"
    "+x = 2\n"
)


@pytest.fixture(autouse=True)
def _no_apply_check(monkeypatch):
    """Keep the apply check (and its repo fetch) out of these tests.

    The re-derivation block calls ``validate_applicability`` via
    ``ensure_repo_root``, which tries a network fetch for the base commit --
    ~80s per test and irrelevant to the scratch filter. The status assertions
    below still exercise the re-derivation itself.
    """
    from config import Config

    monkeypatch.setattr(Config, "APPLY_CHECK_ENABLED", False)


def _make_repo(tmp: Path) -> Path:
    """A committed tmp repo so capture_diff() sees a clean tree (returns "")."""
    repo = tmp / "repo"
    repo.mkdir()
    for args in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(args, cwd=repo, check=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    return repo


class _RawTextStrategy:
    """Returns a Patch whose response is raw text with a diff (the fallback shape)."""

    def __init__(self, response: str):
        self.response = response

    def run(self, issue):
        inf = InferenceResult(
            role="direct",
            response=self.response,
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            finish_reason="stop",
            model="m",
        )
        run = InferenceRun(patch=self.response, inferences=[inf])
        result = ExperimentResult(
            instance_id=issue.instance_id,
            strategy="direct",
            model="m",
            execution=ExecutionResult(run=run),
            cost=CostSummary(
                input_cost_usd=0.0, output_cost_usd=0.0, total_cost_usd=0.0
            ),
            evaluation=EvaluationResult(success=False),
        )
        return Patch(response=self.response), result


def _run_one(tmp_path: Path, response: str) -> dict:
    from experiments.runner import run_experiments

    issue = Issue(
        instance_id="django__django-9999",
        repo=str(_make_repo(tmp_path)),
        base_commit="0" * 40,
        problem_statement="p",
    )
    run_experiments(
        [issue],
        {"direct": _RawTextStrategy(response)},
        base_dir=str(tmp_path),
        provider_name="test",
        model="test-model",
        rate_limit_seconds=0,
    )
    exp_dir = sorted(tmp_path.glob("EXP-*"))[-1]
    rows = [
        json.loads(line)
        for line in (exp_dir / "predictions" / "predictions.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1, f"expected one recorded row, got {rows}"
    return rows[0]


def test_fallback_patch_has_scratch_files_dropped(tmp_path):
    """END-TO-END: the raw-response fallback must not ship scratch files.

    This is the EXP-20261010-062 shape reaching the runner's OTHER path: a clean
    repo (so capture_diff is empty) and a raw response whose scratch chunk makes
    extract_diff fail entirely. Before the runner-side filter, `_exp6.py` was in
    the submitted model_patch.
    """
    row = _run_one(tmp_path, RAW_RESPONSE)
    mp = row["model_patch"]

    assert "_exp6.py" not in mp, (
        "the runner's raw-response fallback shipped an agent scratch file; the "
        "submitted patch must contain the fix and nothing else"
    )
    assert REAL_FILE in mp, "the real fix must survive the filter"
    assert "-x = 1" in mp and "+x = 2" in mp, (
        "the real hunk body must survive byte for byte"
    )
    # The fallback ships the raw text with only the scratch chunk removed, so
    # what remains must be exactly the real file's chunk.
    assert mp == CLEAN_PATCH, (
        "the filtered fallback is not byte-identical to the real chunk; the "
        f"filter changed more than the scratch section:\n{mp!r}"
    )


def test_fallback_still_reports_a_usable_status(tmp_path):
    """The re-derivation must describe the patch that is actually submitted.

    After the scratch chunk is dropped, `model_patch != diff` (diff is empty on
    this path), so the runner re-derives patch_status from the filtered text --
    which must now parse as a well-formed patch. Before the filter, the
    malformed scratch chunk made the whole text MALFORMED_HEADER.
    """
    row = _run_one(tmp_path, RAW_RESPONSE)
    assert row["patch_status"] == "VALID", (
        f"the filtered fallback patch was classified {row['patch_status']!r}; "
        f"with the scratch chunk gone it is a well-formed single-file diff"
    )


def test_fallback_filter_is_idempotent_on_an_already_clean_patch(tmp_path):
    """A patch that never had scratch files must pass through unchanged.

    The tool-calling path already filters in capture_diff; this second call must
    be a byte-level no-op for it (defence in depth, not double damage).
    """
    row = _run_one(tmp_path, CLEAN_PATCH)
    assert row["model_patch"] == CLEAN_PATCH, (
        "an already-clean patch was modified by the second filter"
    )
