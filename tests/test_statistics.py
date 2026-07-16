import pandas as pd
import pytest

from evaluation.statistics import (
    compute_summary,
    compute_success_rate,
    compute_avg_time_per_inference,
    compute_cost_per_success,
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
