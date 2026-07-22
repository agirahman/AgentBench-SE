import pandas as pd
import pytest

from evaluation.statistics import (
    compute_summary,
    compute_success_rate,
    compute_avg_time_per_inference,
    compute_cost_per_success,
    compute_patch_validity_rate,
    compute_patch_quality,
    summarize_run_failure,
    export_statistics_json,
    generate_summary_md,
)


@pytest.fixture
def sample_df():
    """4 rows = 2 strategies × 2 issues, mix of success/error."""
    return pd.DataFrame(
        [
            {
                "instance_id": "django-1",
                "strategy": "direct",
                "execution_time": 2.0,
                "inference_count": 1,
                "total_tokens": 500,
                "cost_usd": 0.001,
                "cost_idr": 16.5,
                "error": "",
                "patch_status": "VALID",
            },
            {
                "instance_id": "django-2",
                "strategy": "direct",
                "execution_time": 3.0,
                "inference_count": 1,
                "total_tokens": 600,
                "cost_usd": 0.002,
                "cost_idr": 33.0,
                "error": "timeout",
                "patch_status": "MALFORMED_HEADER",
            },
            {
                "instance_id": "django-3",
                "strategy": "planning",
                "execution_time": 8.0,
                "inference_count": 2,
                "total_tokens": 1200,
                "cost_usd": 0.004,
                "cost_idr": 66.0,
                "error": "",
                "patch_status": "VALID",
            },
            {
                "instance_id": "django-4",
                "strategy": "planning",
                "execution_time": 10.0,
                "inference_count": 2,
                "total_tokens": 1500,
                "cost_usd": 0.005,
                "cost_idr": 82.5,
                "error": "",
                "patch_status": "NORMALIZE",
            },
        ]
    )


class TestSuccessRate:
    def test_direct_half(self, sample_df):
        sr = compute_success_rate(sample_df)
        assert sr["direct"] == 0.5
        assert sr["planning"] == 1.0


class TestAvgTimePerInference:
    def test_direct(self, sample_df):
        ati = compute_avg_time_per_inference(sample_df)
        # direct: avg_time (2.0+3.0)/2 = 2.5, avg_infs (1+1)/2 = 1 → 2.5
        assert ati["direct"] == pytest.approx(2.5)
        # planning: avg_time (8+10)/2 = 9, avg_infs (2+2)/2 = 2 → 4.5
        assert ati["planning"] == pytest.approx(4.5)


class TestCostPerSuccess:
    def test_direct(self, sample_df):
        cps = compute_cost_per_success(sample_df)
        assert cps.loc["direct", "total_cost_usd"] == pytest.approx(0.003)
        assert cps.loc["direct", "success_count"] == 1
        assert cps.loc["direct", "cost_usd_per_success"] == pytest.approx(0.003)

    def test_planning(self, sample_df):
        cps = compute_cost_per_success(sample_df)
        assert cps.loc["planning", "total_cost_usd"] == pytest.approx(0.009)
        assert cps.loc["planning", "success_count"] == 2
        assert cps.loc["planning", "cost_usd_per_success"] == pytest.approx(0.0045)


class TestSummary:
    def test_has_all_strategies(self, sample_df):
        s = compute_summary(sample_df)
        assert "direct" in s.index
        assert "planning" in s.index
        assert "mean_execution_time" in s.columns
        assert "mean_inference_count" in s.columns


class TestPatchValidityRate:
    def test_direct_half_valid(self, sample_df):
        pvr = compute_patch_validity_rate(sample_df)
        # direct: 1 VALID / 2 = 0.5
        assert pvr["direct"] == pytest.approx(0.5)
        # planning: 2 VALID / 2 = 1.0
        assert pvr["planning"] == pytest.approx(1.0)


class TestFailureBreakdown:
    def test_counts_per_status(self, sample_df):
        fb = summarize_run_failure(sample_df)
        assert fb.loc["direct", "VALID"] == 1
        assert fb.loc["direct", "MALFORMED_HEADER"] == 1
        assert fb.loc["planning", "VALID"] == 1
        assert fb.loc["planning", "NORMALIZE"] == 1


class TestPatchQuality:
    def test_quality_counts(self, sample_df):
        pq = compute_patch_quality(sample_df)
        # direct: 1 VALID + 0 NORMALIZE + 1 INVALID
        assert pq.loc["direct", "valid"] == 1
        assert pq.loc["direct", "normalize"] == 0
        assert pq.loc["direct", "invalid"] == 1
        # planning: 1 VALID + 1 NORMALIZE + 0 INVALID
        assert pq.loc["planning", "valid"] == 1
        assert pq.loc["planning", "normalize"] == 1
        assert pq.loc["planning", "invalid"] == 0

    def test_quality_percentages(self, sample_df):
        pq = compute_patch_quality(sample_df)
        assert pq.loc["direct", "valid_pct"] == 50.0
        assert pq.loc["direct", "normalize_pct"] == 0.0
        assert pq.loc["direct", "invalid_pct"] == 50.0
        assert pq.loc["planning", "valid_pct"] == 50.0
        assert pq.loc["planning", "normalize_pct"] == 50.0
        assert pq.loc["planning", "invalid_pct"] == 0.0


def test_empty_dataframe_exports_summary_files(tmp_path):
    empty_df = pd.DataFrame({"strategy": []})

    out_json = tmp_path / "statistics.json"
    export_statistics_json(empty_df, str(out_json))
    assert out_json.exists()

    out_md = tmp_path / "summary.md"
    generate_summary_md(empty_df, str(out_md))
    assert out_md.exists()
