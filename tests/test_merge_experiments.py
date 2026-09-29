"""Tests for tools/merge_experiments.py."""

import csv
import json
import sys
import tempfile
from pathlib import Path

import pytest

# Add tools/ to path so we can import merge_experiments
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from merge_experiments import merge, _merge_csv, _merge_jsonl


def _make_exp(tmp: Path, name: str, strategies: list[str], instances: list[str]) -> Path:
    d = tmp / name
    (d / "predictions").mkdir(parents=True)

    rows = []
    for strategy in strategies:
        for iid in instances:
            rows.append({
                "instance_id": iid,
                "strategy": strategy,
                "model": "test-model",
                "difficulty": "easy",
                "inference_count": "1",
                "total_tool_calls": "5",
                "api_turns": "3",
                "total_turns": "4",
                "execution_time": "10.0",
                "patch_status": "VALID",
                "apply_status": "APPLICABLE",
            })

    csv_path = d / "generation_result.csv"
    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    for strategy in strategies:
        records = [
            {"instance_id": iid, "model_patch": f"diff_{strategy}", "strategy": strategy}
            for iid in instances
        ]
        jsonl = d / "predictions" / f"{strategy}.jsonl"
        with jsonl.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    return d


class TestMergeExperiments:
    def test_basic_merge_no_overlap(self):
        """Two disjoint sets of instances merge cleanly, no duplicates."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            exp_a = _make_exp(tmp, "EXP-A", ["direct", "planning"], ["iid-001", "iid-002"])
            exp_b = _make_exp(tmp, "EXP-B", ["direct", "planning"], ["iid-003", "iid-004"])
            out = tmp / "MERGED"

            merge(exp_a, exp_b, out, dry_run=False)

            csv_path = out / "generation_result.csv"
            assert csv_path.exists()
            with csv_path.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            assert len(rows) == 8  # 2 strategies × 4 instances

            for strategy in ("direct", "planning"):
                jsonl = out / "predictions" / f"{strategy}.jsonl"
                assert jsonl.exists()
                records = [json.loads(l) for l in jsonl.read_text().splitlines() if l.strip()]
                assert len(records) == 4

    def test_dry_run_writes_nothing(self):
        """--dry-run must not write any output files."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            exp_a = _make_exp(tmp, "EXP-A", ["direct"], ["iid-001"])
            exp_b = _make_exp(tmp, "EXP-B", ["direct"], ["iid-002"])
            out = tmp / "DRYOUT"

            merge(exp_a, exp_b, out, dry_run=True)

            assert not out.exists() or not (out / "generation_result.csv").exists()

    def test_duplicate_detection(self, capsys):
        """Duplicate instance_id+strategy pairs are reported."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            exp_a = _make_exp(tmp, "EXP-A", ["direct"], ["iid-001"])
            exp_b = _make_exp(tmp, "EXP-B", ["direct"], ["iid-001"])  # same instance
            out = tmp / "MERGED"

            merge(exp_a, exp_b, out, dry_run=False)

            captured = capsys.readouterr()
            assert "duplicate" in captured.out.lower()

    def test_missing_strategy_in_one_half(self):
        """A strategy only in one half still appears in the merged output."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            exp_a = _make_exp(tmp, "EXP-A", ["direct", "planning"], ["iid-001"])
            exp_b = _make_exp(tmp, "EXP-B", ["direct"], ["iid-002"])  # no planning
            out = tmp / "MERGED"

            merge(exp_a, exp_b, out, dry_run=False)

            planning_jsonl = out / "predictions" / "planning.jsonl"
            assert planning_jsonl.exists()
            records = [json.loads(l) for l in planning_jsonl.read_text().splitlines() if l.strip()]
            assert len(records) == 1  # only from exp_a

    def test_meta_json_written(self):
        """merge_meta.json must list source dirs and strategies."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            exp_a = _make_exp(tmp, "EXP-A", ["direct"], ["iid-001"])
            exp_b = _make_exp(tmp, "EXP-B", ["planning"], ["iid-002"])
            out = tmp / "MERGED"

            merge(exp_a, exp_b, out, dry_run=False)

            meta = json.loads((out / "merge_meta.json").read_text())
            assert len(meta["merged_from"]) == 2
            assert set(meta["strategies"]) == {"direct", "planning"}
