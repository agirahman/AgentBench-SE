import json
import tempfile
from pathlib import Path

from agents.messages import AgentMessage
from experiments.runner import _load_existing_ids, _append_jsonl, _save_artifacts
from models.inference import InferenceResult


def test_load_existing_ids_skips_invalid_json_lines(tmp_path):
    jsonl_path = tmp_path / "done.jsonl"
    jsonl_path.write_text(
        '{"instance_id": "A", "model_patch": "diff --git a b"}\n'
        'not-json\n'
        '{"instance_id": "B", "model_patch": "diff --git c d"}\n',
        encoding="utf-8",
    )

    result = _load_existing_ids(str(jsonl_path))

    # The malformed line is skipped; the two finished runs are still seen. Note
    # the rows carry a non-empty patch: a row with an empty patch is an errored
    # run, which must be RETRIED by --resume rather than counted as done
    # (see tests/test_resume_skips_failures.py).
    assert result == {"A||False", "B||False"}


def test_append_jsonl_writes_json_lines(tmp_path):
    jsonl_path = tmp_path / "out.jsonl"
    _append_jsonl(str(jsonl_path), {"instance_id": "X", "patch_status": "VALID"})

    data = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(data) == 1
    assert json.loads(data[0])["instance_id"] == "X"


def _inf(role, response):
    return InferenceResult(
        role=role,
        response=response,
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        finish_reason="STOP",
        model="m",
    )


def test_save_artifacts_writes_per_strategy_subfolder(tmp_path):
    inferences = [_inf("direct", "DIRECT-OUTPUT")]
    messages = [AgentMessage(sender="direct", receiver="orchestrator", content="x")]

    _save_artifacts(
        str(tmp_path),
        "ISSUE-1",
        "direct",
        inferences,
        "PATCH",
        messages,
    )

    art_dir = tmp_path / "artifacts" / "ISSUE-1" / "direct"
    assert (art_dir / "direct.md").read_text(encoding="utf-8") == "DIRECT-OUTPUT"
    assert (art_dir / "patch.txt").read_text(encoding="utf-8") == "PATCH"
    assert json.loads((art_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()[0])["sender"] == "direct"


def test_save_artifacts_separates_strategies_no_overwrite(tmp_path):
    _save_artifacts(
        str(tmp_path),
        "ISSUE-1",
        "direct",
        [_inf("direct", "DIRECT")],
        "PATCH-DIRECT",
        [AgentMessage(sender="direct", receiver="orchestrator", content="d")],
    )
    _save_artifacts(
        str(tmp_path),
        "ISSUE-1",
        "review",
        [
            _inf("planner", "PLAN"),
            _inf("executor", "PATCH-INIT"),
            _inf("reviewer", "REVIEW"),
        ],
        "PATCH-REVIEW",
        [
            AgentMessage(sender="planner", receiver="executor", content="p"),
            AgentMessage(sender="executor", receiver="reviewer", content="e"),
            AgentMessage(sender="reviewer", receiver="executor", content="r"),
        ],
    )

    direct_dir = tmp_path / "artifacts" / "ISSUE-1" / "direct"
    review_dir = tmp_path / "artifacts" / "ISSUE-1" / "review"

    assert (direct_dir / "direct.md").read_text(encoding="utf-8") == "DIRECT"
    assert (direct_dir / "patch.txt").read_text(encoding="utf-8") == "PATCH-DIRECT"
    assert len((direct_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()) == 1

    assert (review_dir / "planner.md").read_text(encoding="utf-8") == "PLAN"
    assert (review_dir / "executor.md").read_text(encoding="utf-8") == "PATCH-INIT"
    assert (review_dir / "reviewer.md").read_text(encoding="utf-8") == "REVIEW"
    assert (review_dir / "patch.txt").read_text(encoding="utf-8") == "PATCH-REVIEW"
    assert len((review_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()) == 3


def test_a_second_run_without_data_leaves_empty_records(tmp_path):
    """A retry that produced nothing must not read back as the FIRST run.

    The record files used to be written only when non-empty, so a second run
    with no messages, no trajectory and no tool calls kept run 1's contents on
    disk. A reader then cannot tell "this run made no calls" from "here are the
    calls it made" -- they are the same bytes.
    """
    first = _inf("direct", "FIRST")
    first.tool_calls = [{"name": "read_file", "arguments": "{}", "result": "x"}]
    first.trajectory = [{"type": "assistant", "turn": 1, "content": "FIRST"}]
    _save_artifacts(
        str(tmp_path), "ISSUE-1", "direct", [first], "PATCH-1",
        [AgentMessage(sender="direct", receiver="orchestrator", content="m")],
    )

    art_dir = tmp_path / "artifacts" / "ISSUE-1" / "direct"
    assert (art_dir / "tool_calls.jsonl").read_text(encoding="utf-8").strip()

    # Second run: same pair, but nothing at all was produced.
    _save_artifacts(str(tmp_path), "ISSUE-1", "direct", [_inf("direct", "")], "")

    for name in ("messages.jsonl", "trajectory.jsonl", "tool_calls.jsonl"):
        path = art_dir / name
        assert path.is_file(), f"{name} must exist even when empty"
        assert path.read_text(encoding="utf-8") == "", (
            f"{name} still holds the previous run's contents"
        )
    assert (art_dir / "trajectory.md").read_text(encoding="utf-8").strip() == "# Trajectory"
    assert (art_dir / "patch.txt").read_text(encoding="utf-8") == ""


def test_a_role_missing_from_the_second_run_is_cleaned_up(tmp_path):
    """One artifact folder must not mix two runs' agents.

    ``<role>.md`` was only written for roles present in the run, so a role that
    vanished (e.g. planning -> direct, or a shorter review loop) left its old
    response behind, attributed to the new run. Cleanup is limited to the known
    role names, so trajectory.md / patch.txt / summary.json are untouchable.
    """
    _save_artifacts(
        str(tmp_path), "ISSUE-1", "review",
        [_inf("planner", "PLAN"), _inf("executor", "EXEC"), _inf("reviewer", "REV")],
        "PATCH-1",
        known_roles=["planner", "executor", "reviewer", "direct"],
    )
    review_dir = tmp_path / "artifacts" / "ISSUE-1" / "review"
    (review_dir / "summary.json").write_text("{}", encoding="utf-8")

    _save_artifacts(
        str(tmp_path), "ISSUE-1", "review", [_inf("executor", "EXEC-2")],
        "PATCH-2", known_roles=["planner", "executor", "reviewer", "direct"],
    )

    assert not (review_dir / "planner.md").exists(), "planner was absent: stale file"
    assert not (review_dir / "reviewer.md").exists()
    assert (review_dir / "executor.md").read_text(encoding="utf-8") == "EXEC-2"
    for keep in ("trajectory.md", "patch.txt", "summary.json"):
        assert (review_dir / keep).exists(), f"{keep} must never be cleaned up"


def test_unknown_roles_are_never_deleted(tmp_path):
    """Role files outside the known list are not ours to delete."""
    _save_artifacts(
        str(tmp_path), "ISSUE-1", "direct", [_inf("direct", "D")], "P",
        known_roles=["direct", "planner", "executor", "reviewer"],
    )
    art_dir = tmp_path / "artifacts" / "ISSUE-1" / "direct"
    (art_dir / "custom_note.md").write_text("mine", encoding="utf-8")

    _save_artifacts(
        str(tmp_path), "ISSUE-1", "direct", [_inf("direct", "D2")], "P",
        known_roles=["direct", "planner", "executor", "reviewer"],
    )
    assert (art_dir / "custom_note.md").read_text(encoding="utf-8") == "mine"


def test_a_reasoning_file_is_removed_when_the_role_returns_without_reasoning(tmp_path):
    """The same ghost rule applies to ``<role>_reasoning.md``."""
    first = _inf("executor", "A")
    first.reasoning_content = "deep thought"
    _save_artifacts(str(tmp_path), "ISSUE-1", "review", [first], "P",
                    known_roles=["executor", "planner", "reviewer"])

    art_dir = tmp_path / "artifacts" / "ISSUE-1" / "review"
    assert (art_dir / "executor_reasoning.md").is_file()

    _save_artifacts(str(tmp_path), "ISSUE-1", "review", [_inf("executor", "B")], "P",
                    known_roles=["executor", "planner", "reviewer"])
    assert not (art_dir / "executor_reasoning.md").exists(), (
        "run 2 had no reasoning, so run 1's reasoning must not remain"
    )
    assert (art_dir / "executor.md").read_text(encoding="utf-8") == "B"

