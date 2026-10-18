import pandas as pd
import pytest

from evaluation.statistics import (
    compute_summary,
    compute_success_rate,
    compute_avg_time_per_inference,
    compute_cost_per_success,
    compute_patch_validity_rate,
    compute_patch_quality,
    compute_applyability_rate,
    compute_apply_quality,
    summarize_run_failure,
    export_statistics_json,
    generate_summary_md,
)


@pytest.fixture
def sample_df():
    """4 rows = 2 strategies × 2 issues, mix of success/error.

    USD only: the pipeline carries no IDR columns, so none appear here.
    """
    return pd.DataFrame(
        [
            {
                "instance_id": "django-1",
                "strategy": "direct",
                "execution_time": 2.0,
                "inference_count": 1,
                "total_tokens": 500,
                "cost_usd": 0.001,
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
                "error": "",
                "patch_status": "NORMALIZE",
            },
        ]
    )


@pytest.fixture
def legacy_df_with_idr(sample_df):
    """A CSV from the OLD pipeline: it still has ``*_idr*`` columns.

    The 5 real batches on disk look like this. The IDR figures were computed with
    whatever rate .env held at the time, so they must be IGNORED — reading them
    would mix two rates in one table. The USD column is the source of truth.
    """
    df = sample_df.copy()
    df["cost_idr"] = [16.5, 33.0, 66.0, 82.5]
    df["cost_idr_offpeak"] = [16.5, 33.0, 66.0, 82.5]
    df["cost_idr_actual"] = [16.5, 33.0, 66.0, 82.5]
    return df


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


def test_summary_md_handles_all_failed_strategy(tmp_path):
    """Regression: a strategy with zero successes must not crash the export.

    cost_per_success is NA when success_count is 0 (e.g. every run failed on a
    rate limit). ``float(pd.NA)`` raises TypeError, so formatting must tolerate
    NA instead of aborting the report that a stopped-early run still needs.
    """
    df = pd.DataFrame([
        {"instance_id": "a", "strategy": "direct", "execution_time": 1.0,
         "inference_count": 1, "total_tokens": 10, "cost_usd_offpeak": 0.001,
         "error": "429 rate limit",
         "patch_status": "TIMEOUT", "apply_status": "UNKNOWN"},
    ])
    out = tmp_path / "summary.md"
    generate_summary_md(df, str(out))  # must not raise
    text = out.read_text(encoding="utf-8")
    assert "n/a" in text
    assert "Patch Applicability" in text


def test_cost_per_success_has_no_idr_columns(sample_df):
    """The pipeline emits USD only — no IDR column in the computed table."""
    cps = compute_cost_per_success(sample_df)
    idr_cols = [c for c in cps.columns if "idr" in c.lower()]
    assert idr_cols == [], (
        f"compute_cost_per_success still emits IDR columns {idr_cols}; the rate "
        f"must be applied at reporting time (tools/convert_to_idr.py --rate)"
    )


def test_legacy_idr_columns_are_ignored_not_used(legacy_df_with_idr, sample_df):
    """Old CSVs still carry IDR columns; they must not change any USD result.

    The 5 batches on disk were converted with whatever rate .env held then, so
    those figures are not comparable with each other. Reading them would silently
    mix rates, so the USD column is the only source of truth.
    """
    modern = compute_cost_per_success(sample_df)
    legacy = compute_cost_per_success(legacy_df_with_idr)
    pd.testing.assert_frame_equal(modern, legacy)


def test_statistics_json_records_no_exchange_rate(tmp_path, sample_df):
    """statistics.json must not freeze a rate: it is chosen at reporting time."""
    import json

    out = tmp_path / "statistics.json"
    export_statistics_json(sample_df, str(out))
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "usd_idr_rate" not in data["pricing"], (
        "statistics.json carries an exchange rate, so a batch is frozen to a rate "
        "and batches become incomparable"
    )


def test_summary_md_reports_usd_and_points_to_the_conversion_tool(tmp_path, sample_df):
    """The summary is USD-only, and must tell the reader how to get IDR."""
    out = tmp_path / "summary.md"
    generate_summary_md(sample_df, str(out))
    text = out.read_text(encoding="utf-8")
    assert "Rp" not in text, (
        "the summary still prints Rp figures, which would carry a rate chosen at "
        "sweep time"
    )
    assert "Exchange Rate" not in text, (
        "the summary still prints an exchange rate, which would freeze a batch to it"
    )
    assert "convert_to_idr.py" in text, (
        "the summary must point the reader to the explicit conversion tool"
    )


def test_applyability_rate_counts_only_strictly_applyable(sample_df):
    """NEEDS_FUZZ is an upper-bound bucket and must not count as applyable."""
    df = sample_df.copy()
    df["apply_status"] = ["APPLYABLE", "NOT_APPLYABLE", "NEEDS_FUZZ", "APPLYABLE"]
    rate = compute_applyability_rate(df)
    assert rate["direct"] == pytest.approx(0.5)      # 1 of 2
    assert rate["planning"] == pytest.approx(0.5)    # 1 of 2
    q = compute_apply_quality(df)
    assert q.loc["direct", "applyable_total"] == 1
    assert q.loc["direct", "needs_fuzz"] == 0
    assert q.loc["planning", "needs_fuzz"] == 1


def test_applyability_rate_missing_column_is_safe(sample_df):
    """Old CSVs have no apply_status: the metric must degrade, not raise."""
    rate = compute_applyability_rate(sample_df)
    assert (rate == 0.0).all()
