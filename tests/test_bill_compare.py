"""`read_actual_bill.py --compare` must find our cost in the artefacts we write.

A partner audit found the cross-check silently dead: the extractor looked for a
top-level ``total_cost_usd`` that no artefact has ever contained, so it printed
"no cost field found" on 8 of 8 experiments. RQ3 (cost against the real 9router
bill) therefore could not be checked automatically at all.

The real shapes:
  * ``generation_result.csv`` -- one row per run, column ``cost_usd_actual``. This is
    the authoritative total.
  * ``generation_statistics.json`` -- only MEANS (``summary.<strategy>.mean_cost_usd_actual``),
    so a total needs the per-strategy run count; refuse rather than guess it.
"""

import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from read_actual_bill import _our_total_cost  # noqa: E402


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_csv_rows_are_summed(tmp_path):
    """The per-run CSV is the authoritative total."""
    csv_path = tmp_path / "generation_result.csv"
    _write_csv(csv_path, [
        {"instance_id": "a", "cost_usd_actual": "0.10"},
        {"instance_id": "b", "cost_usd_actual": "0.25"},
        {"instance_id": "c", "cost_usd_actual": "0.05"},
    ])
    stats = tmp_path / "generation_statistics.json"
    stats.write_text("{}", encoding="utf-8")

    total, how = _our_total_cost(stats)
    assert total == pytest.approx(0.40)
    assert "cost_usd_actual" in how
    assert "3 rows" in how


def test_csv_is_preferred_over_statistics(tmp_path):
    """A real run writes both; the CSV must win because the stats only hold means."""
    _write_csv(tmp_path / "generation_result.csv", [
        {"instance_id": "a", "cost_usd_actual": "1.00"},
    ])
    stats = tmp_path / "generation_statistics.json"
    stats.write_text(json.dumps({
        "summary": {"direct": {"mean_cost_usd_actual": 999.0}},
    }), encoding="utf-8")

    total, how = _our_total_cost(stats)
    assert total == pytest.approx(1.00)
    assert "generation_result.csv" in how


def test_means_are_not_reported_as_a_total(tmp_path):
    """Without a run count, a mean must NOT be presented as the total.

    Summing means across strategies would understate the cost by the number of runs
    per strategy -- a wrong number that looks authoritative.
    """
    stats = tmp_path / "generation_statistics.json"
    stats.write_text(json.dumps({
        "summary": {
            "direct": {"mean_cost_usd_actual": 0.03},
            "planning": {"mean_cost_usd_actual": 0.04},
        },
    }), encoding="utf-8")

    total, _ = _our_total_cost(stats)
    assert total is None, "a mean is not a total; the extractor must refuse"


def test_means_are_multiplied_when_the_run_count_is_known(tmp_path):
    """With counts present, means x counts is a legitimate reconstruction."""
    stats = tmp_path / "generation_statistics.json"
    stats.write_text(json.dumps({
        "summary": {"direct": {"mean_cost_usd_actual": 0.03}},
        "run_counts": {"direct": 10},
    }), encoding="utf-8")

    total, how = _our_total_cost(stats)
    assert total == pytest.approx(0.30)
    assert "mean_cost_usd_actual" in how


def test_a_top_level_total_is_still_honoured(tmp_path):
    """Backwards compatibility with any artefact that does write a total."""
    stats = tmp_path / "generation_statistics.json"
    stats.write_text(json.dumps({"total_cost_usd": 12.5}), encoding="utf-8")

    total, how = _our_total_cost(stats)
    assert total == pytest.approx(12.5)
    assert "top level" in how


def test_missing_everything_returns_none_not_zero(tmp_path):
    """Zero would read as "this run was free", which is a false claim."""
    stats = tmp_path / "generation_statistics.json"
    stats.write_text(json.dumps({"summary": {}}), encoding="utf-8")

    total, _ = _our_total_cost(stats)
    assert total is None
