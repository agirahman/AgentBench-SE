"""The response-cache flag must survive a RESUME rebuild.

``_results_from_flat_rows`` reconstructs ``ExperimentResult`` objects from CSV rows
after a merge, and the manifest is built from those objects. The sweep runs in
stages over several days, so the resume path is not hypothetical -- it is how every
batch after the first one is reported.

If the flag is not re-assembled here, a hit recorded in batch 1 would read as clean
in the merged manifest: the CSV would still say True, but the manifest -- the file
that gets read for the headline numbers -- would say no. That is the "silently
loses the signal" failure this whole feature exists to prevent, one layer deeper.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from experiments.runner import _results_from_flat_rows  # noqa: E402


def _row(**overrides):
    """A minimal CSV-shaped row, as pandas would hand it over."""
    row = {
        "instance_id": "django__django-10914",
        "strategy": "direct",
        "model": "cbai/deepseek-v4.1-flash",
        "total_tokens": 1000,
        "input_tokens_total": 900,
        "input_tokens_cached": 700,
        "input_tokens_regular": 200,
        "output_tokens": 100,
        "cost_usd_offpeak": 0.01,
        "execution_time": 12.5,
        "total_turns": 3,
        "timestamp": "2026-10-04T00:00:00+00:00",
        "generated": True,
        "patch_status": "VALID",
    }
    row.update(overrides)
    return row


def test_rebuild_keeps_a_recorded_hit():
    """semantic_cache_hit=True in the CSV must still be True after the rebuild."""
    rebuilt = _results_from_flat_rows([_row(semantic_cache_hit=True,
                                           semantic_cache_hit_turns=3,
                                           semantic_cache_cost_saved_usd=0.02)])

    assert len(rebuilt) == 1
    cost = rebuilt[0].cost
    assert cost.semantic_cache_hit is True, (
        "the hit was lost in the CSV->object rebuild; a resumed experiment would "
        "report this run as cache-clean"
    )
    assert cost.semantic_cache_hit_turns == 3
    assert cost.semantic_cache_cost_saved_usd == pytest.approx(0.02)


def test_rebuild_keeps_a_clean_row_clean():
    """An explicit False must stay False -- no invented hits on resume."""
    rebuilt = _results_from_flat_rows([_row(semantic_cache_hit=False,
                                           semantic_cache_hit_turns=0,
                                           semantic_cache_cost_saved_usd=0.0)])
    assert rebuilt[0].cost.semantic_cache_hit is False
    assert rebuilt[0].cost.semantic_cache_hit_turns == 0


def test_rebuild_tolerates_rows_without_the_column():
    """A pre-fix CSV has no such column at all: no crash, and not a hit.

    Batch 1 was written before the flag existed, so this is the shape of every row
    already on disk. Treating the absent column as a hit would flag the whole of
    batch 1; crashing would break resume entirely.
    """
    row = _row()
    assert "semantic_cache_hit" not in row

    rebuilt = _results_from_flat_rows([row])

    assert rebuilt[0].cost.semantic_cache_hit is False
    assert rebuilt[0].cost.semantic_cache_hit_turns == 0
    assert rebuilt[0].cost.semantic_cache_cost_saved_usd == 0.0


def test_rebuild_treats_blank_cells_as_not_a_hit():
    """Blank cells arrive as NaN from pandas; bool(NaN) is True, so this is a trap.

    A blank means "not recorded" (savepoint recovery, or a pre-fix row). It must
    NOT read as a hit -- that would invent a contamination on every recovered row.
    """
    import pandas as pd

    rebuilt = _results_from_flat_rows([_row(
        semantic_cache_hit=float("nan"),
        semantic_cache_hit_turns=float("nan"),
        semantic_cache_cost_saved_usd=float("nan"),
    )])

    assert rebuilt[0].cost.semantic_cache_hit is False, (
        "a NaN flag was read as a hit (bool(NaN) is True)"
    )
    assert rebuilt[0].cost.semantic_cache_hit_turns == 0
    assert pd.isna(rebuilt[0].cost.semantic_cache_cost_saved_usd) or \
        rebuilt[0].cost.semantic_cache_cost_saved_usd == 0.0


def test_rebuild_accepts_string_booleans_from_csv():
    """CSV round-trips booleans as text; 'True'/'False' must both be understood."""
    for text, expected in (("True", True), ("False", False),
                           ("true", True), ("false", False), ("1", True), ("0", False)):
        rebuilt = _results_from_flat_rows([_row(semantic_cache_hit=text)])
        assert rebuilt[0].cost.semantic_cache_hit is expected, (
            f"CSV cell {text!r} was read as "
            f"{rebuilt[0].cost.semantic_cache_hit!r}, expected {expected!r}"
        )


def test_rebuild_flag_reaches_the_manifest():
    """The end of the chain: a rebuilt hit must appear in the manifest.

    The manifest is what gets quoted for the headline numbers, so this is the
    assertion that actually protects the reported result.
    """
    from experiments.observability import build_experiment_manifest

    rebuilt = _results_from_flat_rows([
        _row(instance_id="i1", strategy="direct", semantic_cache_hit=True,
             semantic_cache_hit_turns=2, semantic_cache_cost_saved_usd=0.03),
        _row(instance_id="i2", strategy="planning", semantic_cache_hit=False),
    ])
    manifest = build_experiment_manifest(
        experiment_id="EXP-RESUME-TEST",
        provider_name="fake",
        issues=[],
        strategies=["direct", "planning"],
        agents=[],
        output_dir=".",
        results=rebuilt,
    )

    assert manifest["summary"]["semantic_cache_hits"] == 1, (
        "the rebuilt hit did not reach the manifest headline"
    )
    assert manifest["results"][0]["semantic_cache_hit"] is True
    assert manifest["results"][1]["semantic_cache_hit"] is False
