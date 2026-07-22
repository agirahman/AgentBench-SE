import json
import tempfile
from pathlib import Path

from experiments.runner import _load_existing_ids, _append_jsonl


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
