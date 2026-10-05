"""`_merge_csv_rows` must never lose a batch's paid-for rows when a sweep resumes.

Why this is load-bearing: the 150-run sweep is staged across days
(`--limit 10` -> `--limit 20 --resume` -> ...). Each stage ends by writing
`generation_result.csv`, and the CSV is written from ``all_results`` -- the runs
started in THIS process only. Without a merge, stage N's export OVERWRITES stages
1..N-1 and silently drops their patches, so the thesis scores would be computed
from a fraction of the work with no visible error. Measured independently by two
auditors: a 3-issue pass followed by a 5-issue resume left 2 rows out of 5.

The function is only exercised by ``tools/verify_merge_safety.py`` today, which
(a) must be run by hand and (b) only tests a round-trip with ``new_rows=[]`` -- it
never tests the actual resume case (an old CSV plus this session's new rows). These
tests pin the real contract.

Each test below was checked against a TARGETED mutation of the logic it guards:
the mutation was applied, the test was observed to fail, and the source was restored
byte-for-byte (SHA256 verified). "Targeted" is deliberate -- this is 6 targeted
mutations, not a claim that a mutation of every line would be caught.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest
from loguru import logger as loguru_logger

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from experiments.runner import _merge_csv_rows  # noqa: E402

STRATS = ["direct", "planning", "review"]


def _row(iid, strategy, tokens, cost):
    """A minimal CSV row with the columns the merge keys and preserves."""
    return {
        "instance_id": iid,
        "strategy": strategy,
        "total_tokens": tokens,
        "cost_usd_actual": cost,
    }


def _write_csv(path: Path, rows, header="instance_id,strategy,total_tokens,cost_usd_actual"):
    lines = [header]
    for r in rows:
        lines.append(f"{r['instance_id']},{r['strategy']},"
                     f"{r['total_tokens']},{r['cost_usd_actual']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _savepoint(pred_dir: Path, strategy: str, iid: str):
    """Append one savepoint row (the runner's per-run record)."""
    pred_dir.mkdir(parents=True, exist_ok=True)
    with (pred_dir / f"{strategy}.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"instance_id": "%s", "patch_status": "VALID", '
                '"model_patch": "x"}\n' % iid)


def _by_key(rows):
    return {(r["instance_id"], r["strategy"]): r for r in rows}


@pytest.fixture
def captured_logs():
    """Collect WARNING+ messages from the project's loguru logger.

    The runner logs via ``utils.logger`` (loguru), which does NOT propagate to the
    stdlib ``logging`` root, so pytest's ``caplog`` cannot see it. A temporary sink
    is the reliable way to assert on the message.
    """
    messages: list[str] = []
    sink_id = loguru_logger.add(lambda m: messages.append(m.record["message"]),
                                level="WARNING")
    try:
        yield messages
    finally:
        loguru_logger.remove(sink_id)


# --------------------------------------------------------------------------
# 1. The actual resume case: an old CSV + this session's new rows
# --------------------------------------------------------------------------

def test_merge_preserves_previous_batch_rows(tmp_path):
    """Batch 1 (issues 1-2 x 3 strategies) + batch 2 (issues 3-4 x 3) = 12 rows.

    Batch 1's values (token/cost) must come through UNCHANGED and with no
    duplicates -- a merge that retyped a column or dropped a row would trade the
    data-loss bug for a quieter data-corruption bug.
    """
    csv_path = tmp_path / "generation_result.csv"
    pred_dir = tmp_path / "predictions"

    batch1 = [_row(iid, s, tokens=100 + i, cost=0.10 + i / 100)
              for i, iid in enumerate(("django__django-1", "django__django-2"))
              for s in STRATS]
    _write_csv(csv_path, batch1)
    for r in batch1:
        _savepoint(pred_dir, r["strategy"], r["instance_id"])

    batch2 = [_row(iid, s, tokens=900 + i, cost=0.90 + i / 100)
              for i, iid in enumerate(("django__django-3", "django__django-4"))
              for s in STRATS]

    merged = _merge_csv_rows(str(csv_path), batch2, pred_dir, list(STRATS))

    assert len(merged) == 12, f"expected 12 rows (6 old + 6 new), got {len(merged)}"
    keys = [(r["instance_id"], r["strategy"]) for r in merged]
    assert len(keys) == len(set(keys)), "the merge produced a duplicate key"

    got = _by_key(merged)
    for r in batch1:  # batch 1 survived byte-for-byte
        m = got[(r["instance_id"], r["strategy"])]
        assert float(m["total_tokens"]) == r["total_tokens"]
        assert float(m["cost_usd_actual"]) == pytest.approx(r["cost_usd_actual"])
    for r in batch2:  # batch 2 present
        assert (r["instance_id"], r["strategy"]) in got


# --------------------------------------------------------------------------
# 2. A retry must replace, not duplicate, and the NEWEST wins
# --------------------------------------------------------------------------

def test_merge_retry_newest_wins(tmp_path):
    """Re-running one (instance, strategy) leaves ONE row, carrying the new values.

    A duplicate row here would make the evaluation wrapper count the instance
    twice (measured at 150% for one duplicate -- see eval_modal.py:154-169).
    """
    csv_path = tmp_path / "generation_result.csv"
    pred_dir = tmp_path / "predictions"
    old = _row("django__django-1", "direct", tokens=100, cost=0.10)
    _write_csv(csv_path, [old])
    _savepoint(pred_dir, "direct", "django__django-1")

    # The retry: same key, different (newer) numbers.
    new = _row("django__django-1", "direct", tokens=777, cost=0.77)
    merged = _merge_csv_rows(str(csv_path), [new], pred_dir, ["direct"])

    assert len(merged) == 1, f"a retry must not duplicate the row, got {len(merged)}"
    assert float(merged[0]["total_tokens"]) == 777, "the NEW row must win"
    assert float(merged[0]["cost_usd_actual"]) == pytest.approx(0.77)


# --------------------------------------------------------------------------
# 3. No existing CSV: only this session's rows
# --------------------------------------------------------------------------

def test_merge_with_no_existing_csv_returns_new_rows(tmp_path):
    """A first run has no old CSV; the output is exactly the new rows."""
    csv_path = tmp_path / "generation_result.csv"     # deliberately not created
    pred_dir = tmp_path / "predictions"
    pred_dir.mkdir()                                  # exists but has no savepoints
    new = [_row("django__django-1", s, tokens=10, cost=0.01) for s in STRATS]

    merged = _merge_csv_rows(str(csv_path), new, pred_dir, list(STRATS))

    assert len(merged) == 3
    assert {(r["instance_id"], r["strategy"]) for r in merged} == {
        ("django__django-1", s) for s in STRATS
    }


# --------------------------------------------------------------------------
# 4. Unparseable CSV: recover from savepoints, and do NOT fabricate cost 0
# --------------------------------------------------------------------------

def test_merge_unparseable_csv_falls_back_to_savepoints(tmp_path):
    """A CSV pandas cannot read at all (both strict and on_bad_lines='skip').

    The savepoints are the source of truth for WHICH runs happened. They do not
    record tokens/cost, so those come back as None and the row is flagged
    ``_recovered_from_savepoint``. A fabricated 0.00 for a run that really spent
    money is a WRONG number, not a missing one -- the same class of error as the
    cache-discount bug that nearly halved RQ3.
    """
    csv_path = tmp_path / "generation_result.csv"
    pred_dir = tmp_path / "predictions"
    # Invalid UTF-8: raises UnicodeDecodeError for BOTH parse attempts.
    csv_path.write_bytes(b"\xff\xfe\x00 not a csv at all")
    _savepoint(pred_dir, "direct", "django__django-1")

    merged = _merge_csv_rows(str(csv_path), [], pred_dir, ["direct"])

    assert len(merged) == 1, f"the savepoint row must be recovered, got {len(merged)}"
    row = merged[0]
    assert row["instance_id"] == "django__django-1"
    assert row["strategy"] == "direct"
    assert row.get("_recovered_from_savepoint") is True
    # Cost/tokens are "not recorded", NOT a fake zero.
    assert row["cost_usd_actual"] is None
    assert row["total_tokens"] is None
    assert row["cost_usd_actual"] != 0


# --------------------------------------------------------------------------
# 5. A row whose strategy is unexpected is KEPT and WARNED about -- never dropped
# --------------------------------------------------------------------------

def test_merge_keeps_unexpected_strategy_row_and_warns(tmp_path, captured_logs):
    """An unexpected strategy is reported, NOT deleted.

    A CSV row can carry a strategy we neither requested nor have a savepoint for.
    A DROP-based filter for such rows was tried in an UNCOMMITTED draft and
    rejected: it would have deleted real rows when a ``<strategy>.jsonl`` is
    missing (measured), while guarding a truncation that 29 real CSVs never
    exhibited. It was never committed -- ``git log --all -S"dropped_ghosts"`` and
    ``-S"allowed_strategies"`` are empty -- so it is not part of this file's
    history. That is the wrong default in a function whose whole purpose is to
    prevent data loss: measured, a missing ``<strategy>.jsonl`` would silently
    delete real rows. Measured across 29 real CSVs (336 rows), strategy is only
    ever direct/planning/review and no file lacks a trailing newline -- and the CSV
    is written atomically -- so the "truncated ghost row" that filter guarded is
    not a real failure mode.

    So the contract is now KEEP + WARN: the row survives, and the warning names the
    instance/strategy so an operator can decide. (This test does not depend on WHY
    the strategy is unexpected -- truncation or a missing savepoint are the same
    path.)
    """
    csv_path = tmp_path / "generation_result.csv"
    pred_dir = tmp_path / "predictions"

    valid = [_row(iid, s, tokens=100, cost=0.10)
             for iid in ("django__django-1", "django__django-2") for s in STRATS]
    lines = ["instance_id,strategy,total_tokens,cost_usd_actual"]
    lines += [f"{r['instance_id']},{r['strategy']},100,0.10" for r in valid]
    lines.append("django__django-3,dire")            # unexpected strategy
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for r in valid:
        _savepoint(pred_dir, r["strategy"], r["instance_id"])

    merged = _merge_csv_rows(str(csv_path), [], pred_dir, list(STRATS))

    # KEPT: all 7 rows survive, including the unexpected one.
    assert len(merged) == 7, (
        f"the unexpected row must be KEPT (7 rows), got {len(merged)}"
    )
    assert ("django__django-3", "dire") in _by_key(merged), (
        "an unexpected-strategy row was dropped -- this function must never lose "
        "data by deleting rows"
    )
    # And the operator was told.
    assert any("django__django-3/dire" in m for m in captured_logs), (
        f"the warning must name the row; captured: {captured_logs}"
    )
    assert any("KEPT" in m or "kept" in m for m in captured_logs), (
        f"the warning must say the row was kept; captured: {captured_logs}"
    )


def test_merge_keeps_csv_rows_when_savepoint_is_missing(tmp_path, captured_logs):
    """R2: a real row must SURVIVE when its ``<strategy>.jsonl`` is gone.

    The data-loss path a DROP-based filter opens: a row is dropped when its strategy
    is neither requested NOR backed by a savepoint. That happens on a SUBSET resume
    -- ``--strategies direct`` on an experiment whose earlier batches wrote
    planning/review rows -- when those strategies' ``.jsonl`` files are missing
    (cleaned up, or lost to a bug). The CSV still holds genuine results, so they
    must be kept: this function exists to PREVENT loss, and the savepoint fallback
    cannot restore them (it only reads the strategies it is asked for, and a missing
    file has nothing to read).

    Measured against that draft DROP filter (uncommitted, since removed): the
    planning and review rows vanished from the merged output -- real data deleted to
    guard a corruption (a truncated CSV) that 29 real CSVs never exhibited. The
    measurement is real; only the filter's provenance differs: a rejected draft,
    never committed.
    """
    csv_path = tmp_path / "generation_result.csv"
    pred_dir = tmp_path / "predictions"

    # The CSV holds all three strategies (written by an earlier full batch)...
    rows = [_row("django__django-1", s, tokens=100, cost=0.10) for s in STRATS]
    _write_csv(csv_path, rows)
    # ...but only direct.jsonl exists; planning.jsonl and review.jsonl are gone,
    # and this resume asks for `direct` ONLY (a subset resume).
    _savepoint(pred_dir, "direct", "django__django-1")

    merged = _merge_csv_rows(str(csv_path), [], pred_dir, ["direct"])

    got = _by_key(merged)
    assert len(merged) == 3, (
        f"all three CSV rows must survive a missing savepoint, got {len(merged)}"
    )
    for s in STRATS:
        assert ("django__django-1", s) in got, (
            f"the {s!r} row was dropped because its savepoint is missing -- "
            f"this is the exact data loss the merge exists to prevent"
        )
    # The situation is reported, not silent.
    assert any("planning" in m or "review" in m for m in captured_logs), (
        f"the missing-savepoint rows must be warned about; captured: {captured_logs}"
    )


def test_merge_does_not_warn_when_savepoints_cannot_be_read(tmp_path, captured_logs):
    """No warning when the savepoint dir is missing -- we cannot judge, so stay quiet.

    The warning is only honest when we actually looked at the savepoints. If
    ``pred_dir`` is a Path that does NOT exist, ``glob("*.jsonl")`` returns nothing,
    so every non-requested strategy would appear savepoint-less and we would warn
    about a row we never checked. A comment once said the check required a pred_dir
    that "exists"; the code only tested ``is not None``, so a missing dir produced a
    false warning (measured). The guard now requires ``is_dir()``.

    The row itself must STILL be kept -- only the spurious warning is suppressed.
    """
    csv_path = tmp_path / "generation_result.csv"
    # CSV has a `planning` row, but this resume only requests `direct`.
    _write_csv(csv_path, [_row("django__django-1", s, tokens=100, cost=0.10)
                          for s in ("direct", "planning")])
    missing_pred = tmp_path / "predictions"          # deliberately NOT created

    merged = _merge_csv_rows(str(csv_path), [], missing_pred, ["direct"])

    assert len(merged) == 2, "rows must still be kept when the savepoints are unreadable"
    assert not any("neither requested nor backed" in m for m in captured_logs), (
        f"a missing savepoint dir must not produce a judgement we cannot make: "
        f"{captured_logs}"
    )


def test_merge_does_not_warn_when_pred_dir_is_a_file(tmp_path, captured_logs):
    """A pred_dir that is a FILE must stay silent -- this is what pins ``is_dir()``.

    The test above ("missing pred_dir") passes with either ``is_dir()`` or
    ``exists()``: a non-existent path is False for BOTH, so it cannot tell them
    apart. The two only diverge when the path EXISTS but is not a directory. With
    ``exists()`` a file passes the guard, ``glob("*.jsonl")`` on a file yields
    nothing, every non-requested strategy looks savepoint-less, and a FALSE warning
    is emitted -- measured: exists() -> 1 warning, is_dir() -> 0. So this test locks
    the stronger check: we only warn when we can actually READ a directory of
    savepoints.

    The rows must still be KEPT (only the spurious warning is suppressed).
    """
    csv_path = tmp_path / "generation_result.csv"
    # CSV has a `planning` row, but this resume only requests `direct`.
    _write_csv(csv_path, [_row("django__django-1", s, tokens=100, cost=0.10)
                          for s in ("direct", "planning")])
    # pred_dir is a FILE, not a directory: exists() is True, is_dir() is False.
    pred_as_file = tmp_path / "predictions"
    pred_as_file.write_text("not a directory\n", encoding="utf-8")

    merged = _merge_csv_rows(str(csv_path), [], pred_as_file, ["direct"])

    assert len(merged) == 2, "rows must still be kept when the savepoints are unreadable"
    assert not captured_logs, (
        f"a file (not dir) at pred_dir must not produce a judgement we cannot make; "
        f"got warnings: {captured_logs}"
    )


# --------------------------------------------------------------------------
# 6. The header carries a leading "[" (a pandas artifact of the writer)
# --------------------------------------------------------------------------

def test_merge_reads_header_with_leading_bracket(tmp_path):
    """The original writer left a "[" on the first column name.

    If the merge does not strip it, ``record.get("instance_id")`` is None and the
    old row is keyed under a bogus name -- so a retry for the same instance is
    seen as NEW and the file ends up with a duplicate.
    """
    csv_path = tmp_path / "generation_result.csv"
    pred_dir = tmp_path / "predictions"
    _write_csv(csv_path,
               [_row("django__django-1", "direct", tokens=100, cost=0.10)],
               header="[instance_id,strategy,total_tokens,cost_usd_actual")
    _savepoint(pred_dir, "direct", "django__django-1")

    new = _row("django__django-1", "direct", tokens=777, cost=0.77)
    merged = _merge_csv_rows(str(csv_path), [new], pred_dir, ["direct"])

    assert len(merged) == 1, (
        f"the '['-prefixed header must be recognised, got {len(merged)} rows"
    )
    assert merged[0]["instance_id"] == "django__django-1"
    assert float(merged[0]["total_tokens"]) == 777
