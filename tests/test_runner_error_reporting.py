import json
import tempfile
from pathlib import Path

from agents.messages import AgentMessage
from experiments.runner import _load_existing_ids, _append_jsonl, _save_artifacts
from models.inference import InferenceResult


def test_load_existing_ids_skips_invalid_json_lines(tmp_path):
    jsonl_path = tmp_path / "done.jsonl"
    jsonl_path.write_text(
        '{"instance_id": "A"}\n'
        'not-json\n'
        '{"instance_id": "B"}\n',
        encoding="utf-8",
    )

    result = _load_existing_ids(str(jsonl_path))

    assert result == {"A", "B"}


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

