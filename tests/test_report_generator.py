import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from eval_modal import extract_failure_reason, load_instance_report, enrich_instance_result
from evaluation.report_generator import (
    _build_failure_breakdown,
    load_eval_results,
    merge_data,
    generate_reports,
)


APPLY_FAIL_LOG = """2026-08-13 15:38:51,096 - INFO - Failed to apply patch to container, trying again...
2026-08-13 15:38:51,518 - INFO - >>>>> Patch Apply Failed:
patching file django/conf/global_settings.py
Hunk #1 FAILED at 100.
1 out of 1 hunk FAILED -- saving rejects to file django/conf/global_settings.py.rej
2026-08-13 15:38:51,535 - INFO - Traceback (most recent call last):
  File "/root/swebench/harness/modal_eval/run_evaluation_modal.py", line 282, in run_instance_modal
    raise EvaluationError(
swebench.harness.utils.EvaluationError: django__django-10914: >>>>> Patch Apply Failed:
"""

TIMEOUT_LOG = """2026-08-13 15:39:00,000 - INFO - Test runtime: 1800 > 600 seconds
"""


def test_extract_failure_reason_apply_patch_fail(tmp_path):
    log = tmp_path / "run_instance.log"
    log.write_text(APPLY_FAIL_LOG, encoding="utf-8")
    reason = extract_failure_reason(log)
    assert reason is not None
    assert reason.startswith("APPLY_PATCH_FAIL:")
    assert "Hunk #1 FAILED at 100" in reason
    assert "Traceback" not in reason


def test_extract_failure_reason_timeout(tmp_path):
    log = tmp_path / "run_instance.log"
    log.write_text(TIMEOUT_LOG, encoding="utf-8")
    assert extract_failure_reason(log) == "TESTS_TIMEOUT"


def test_extract_failure_reason_missing_file(tmp_path):
    assert extract_failure_reason(tmp_path / "none.log") is None


def test_load_instance_report(tmp_path):
    log_dir = tmp_path
    report = {"django__django-11001": {"resolved": True, "patch_successfully_applied": True}}
    (log_dir / "django__django-11001").mkdir()
    (log_dir / "django__django-11001" / "report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )
    data = load_instance_report(log_dir, "django__django-11001")
    assert data["patch_successfully_applied"] is True
    assert load_instance_report(log_dir, "missing-1") == {}


def test_enrich_instance_result_resolved(tmp_path):
    result = enrich_instance_result("django__django-11001", True, tmp_path)
    assert result["resolved"] is True
    assert result["patch_applied"] is True
    assert result["failure_reason"] is None


def test_enrich_instance_result_apply_fail(tmp_path):
    inst_dir = tmp_path / "django__django-10914"
    inst_dir.mkdir()
    (inst_dir / "run_instance.log").write_text(APPLY_FAIL_LOG, encoding="utf-8")
    result = enrich_instance_result("django__django-10914", False, tmp_path)
    assert result["resolved"] is False
    assert result["patch_applied"] is False
    assert result["failure_reason"].startswith("APPLY_PATCH_FAIL:")


def test_enrich_instance_result_tests_error(tmp_path):
    inst_dir = tmp_path / "django__django-1"
    inst_dir.mkdir()
    report = {
        "django__django-1": {
            "resolved": False,
            "patch_successfully_applied": True,
            "tests_status": {
                "FAIL_TO_PASS": {"success": [], "failure": ["tests.test_foo::test_bar"]},
                "PASS_TO_PASS": {"success": ["tests.test_foo::test_ok"], "failure": []},
            },
        }
    }
    (inst_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    result = enrich_instance_result("django__django-1", False, tmp_path)
    assert result["failure_reason"] == "TESTS_ERROR"


def _sample_df():
    return pd.DataFrame(
        [
            {
                "instance_id": "django__django-1",
                "strategy": "direct",
                "model": "m",
                "difficulty": "easy",
                "execution_time": 1.0,
                "total_tokens": 100,
                "cost_usd": 0.001,
            },
            {
                "instance_id": "django__django-1",
                "strategy": "planning",
                "model": "m",
                "difficulty": "easy",
                "execution_time": 2.0,
                "total_tokens": 200,
                "cost_usd": 0.002,
            },
            {
                "instance_id": "django__django-2",
                "strategy": "direct",
                "model": "m",
                "difficulty": "hard",
                "execution_time": 3.0,
                "total_tokens": 300,
                "cost_usd": 0.003,
            },
        ]
    )


def _sample_eval_results():
    return {
        "direct": {
            "results": [
                {"instance_id": "django__django-1", "resolved": True, "patch_applied": True, "failure_reason": None},
                {"instance_id": "django__django-2", "resolved": False, "patch_applied": False, "failure_reason": "APPLY_PATCH_FAIL: Hunk #1 FAILED at 100"},
            ]
        },
        "planning": {
            "results": [
                {"instance_id": "django__django-1", "resolved": False, "patch_applied": True, "failure_reason": "TESTS_ERROR"},
            ]
        },
    }


def test_merge_data_adds_columns():
    df = merge_data(_sample_df(), _sample_eval_results())
    assert "patch_applied" in df.columns
    assert "failure_reason" in df.columns
    row_direct = df[df["strategy"] == "direct"].set_index("instance_id")
    assert row_direct.loc["django__django-1", "resolved"] == True
    assert row_direct.loc["django__django-1", "patch_applied"] == True
    assert row_direct.loc["django__django-2", "resolved"] == False
    assert row_direct.loc["django__django-2", "patch_applied"] == False
    assert row_direct.loc["django__django-2", "failure_reason"].startswith("APPLY_PATCH_FAIL")
    row_planning = df[df["strategy"] == "planning"].set_index("instance_id")
    assert row_planning.loc["django__django-1", "resolved"] == False
    assert row_planning.loc["django__django-1", "patch_applied"] == True
    assert row_planning.loc["django__django-1", "failure_reason"] == "TESTS_ERROR"


def test_build_failure_breakdown():
    df = merge_data(_sample_df(), _sample_eval_results())
    breakdown = _build_failure_breakdown(df)
    direct = breakdown[breakdown["strategy"] == "direct"].iloc[0]
    assert direct["resolved"] == 1
    assert "APPLY_PATCH_FAIL: Hunk #1 FAILED at 100" in breakdown.columns
    assert direct["APPLY_PATCH_FAIL: Hunk #1 FAILED at 100"] == 1
    planning = breakdown[breakdown["strategy"] == "planning"].iloc[0]
    assert planning["TESTS_ERROR"] == 1


def test_generate_reports_writes_breakdown(tmp_path):
    df = merge_data(_sample_df(), _sample_eval_results())
    eval_dir = generate_reports(df, tmp_path)

    results_csv = pd.read_csv(eval_dir / "results.csv")
    assert "patch_applied" in results_csv.columns
    assert "failure_reason" in results_csv.columns

    breakdown_csv = pd.read_csv(eval_dir / "failure_breakdown.csv")
    assert "strategy" in breakdown_csv.columns
    assert "resolved" in breakdown_csv.columns
    assert "TESTS_ERROR" in breakdown_csv.columns

    stats = json.loads((eval_dir / "statistics.json").read_text(encoding="utf-8"))
    assert "failure_breakdown" in stats

    summary_md = (eval_dir / "summary.md").read_text(encoding="utf-8")
    assert "## Failure Analysis" in summary_md
    assert "Patch Application Rate" in summary_md
    assert "Failure Reason Breakdown" in summary_md


def test_load_eval_results_reads_files(tmp_path):
    pred_dir = tmp_path / "predictions"
    pred_dir.mkdir()
    (pred_dir / "direct_results.json").write_text(
        json.dumps({"results": [{"instance_id": "x-1", "resolved": True}]}),
        encoding="utf-8",
    )
    results = load_eval_results(tmp_path)
    assert "direct" in results
    assert results["direct"]["results"][0]["resolved"] is True
