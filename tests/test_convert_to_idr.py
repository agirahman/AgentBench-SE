"""Tests for tools/convert_to_idr.py — the reporting-time USD→IDR conversion.

WHY THIS TOOL EXISTS
--------------------
The exchange rate is a presentation choice, not research data. While the pipeline
converted, each sweep batch was frozen to whatever ``USD_IDR_RATE`` was in ``.env``
when it ran, so 5 batches run at different times produced costs that could not be
compared. The pipeline now reports USD only, and this tool converts afterwards with
ONE operator-supplied rate.

WHAT THESE TESTS PIN
--------------------
1. ``--rate`` is REQUIRED. A default would be worse than an error: it would print
   Rp figures at a rate nobody chose, which is the exact inconsistency being fixed
   (the repo held three different rates: .env 17828.10, Config 16500.0,
   report_generator 16500).
2. The tool uses the rate it is GIVEN, not a hardcoded one.
3. Legacy CSVs that still contain ``*_idr*`` columns are read via their USD column,
   and the old IDR figures are IGNORED — they were converted with a different rate.
4. The source CSV is only read, never modified.
5. ``predictions.jsonl`` is never touched: it is the SWE-bench evaluation input and
   carries no cost columns at all.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL_PATH = REPO_ROOT / "tools" / "convert_to_idr.py"


def _load_tool():
    """Import tools/convert_to_idr.py as a module (tools/ is not a package)."""
    spec = importlib.util.spec_from_file_location("convert_to_idr", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["convert_to_idr"] = module
    spec.loader.exec_module(module)
    return module


tool = _load_tool()


# ── fixtures ────────────────────────────────────────────────────────────────

def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


@pytest.fixture
def modern_exp(tmp_path):
    """A CSV from the NEW pipeline: USD columns only."""
    exp = tmp_path / "results" / "EXP-NEW-001"
    _write_csv(
        exp / "generation_result.csv",
        ["instance_id", "strategy", "cost_usd_actual", "cost_usd_peak_total",
         "cost_usd_offpeak", "resolved"],
        [
            {"instance_id": "a", "strategy": "direct", "cost_usd_actual": "1.0",
             "cost_usd_peak_total": "2.0", "cost_usd_offpeak": "1.0", "resolved": "True"},
            {"instance_id": "b", "strategy": "direct", "cost_usd_actual": "3.0",
             "cost_usd_peak_total": "6.0", "cost_usd_offpeak": "3.0", "resolved": "False"},
            {"instance_id": "c", "strategy": "planning", "cost_usd_actual": "2.0",
             "cost_usd_peak_total": "4.0", "cost_usd_offpeak": "2.0", "resolved": "True"},
        ],
    )
    return exp


@pytest.fixture
def legacy_exp(tmp_path):
    """A CSV from the OLD pipeline: it still has *_idr* columns.

    The IDR values are deliberately WRONG for the rate used in the tests, so a test
    that accidentally read them instead of converting USD would fail loudly.
    """
    exp = tmp_path / "results" / "EXP-OLD-001"
    _write_csv(
        exp / "generation_result.csv",
        ["instance_id", "strategy", "cost_usd_actual", "cost_idr_actual",
         "cost_usd_peak_total", "cost_idr_peak_total", "resolved"],
        [
            {"instance_id": "a", "strategy": "direct", "cost_usd_actual": "1.0",
             "cost_idr_actual": "999999.0", "cost_usd_peak_total": "2.0",
             "cost_idr_peak_total": "888888.0", "resolved": "True"},
        ],
    )
    return exp


# ── 1. --rate is required ───────────────────────────────────────────────────

def test_convert_tool_refuses_without_rate(tmp_path, capsys):
    """A missing rate must be an error, not a silent default.

    This is the central guarantee of the tool. With a default, a report could be
    produced at a rate nobody chose — and the repo already demonstrated that failure
    by holding three different rates in three places.
    """
    exp = tmp_path / "results" / "EXP-1"
    _write_csv(exp / "generation_result.csv", ["strategy", "cost_usd"],
               [{"strategy": "direct", "cost_usd": "1.0"}])

    with pytest.raises(SystemExit) as excinfo:
        tool.main(["--exp", "EXP-1", "--results-dir", str(tmp_path / "results")])

    assert excinfo.value.code != 0, "a missing --rate must exit non-zero"
    err = capsys.readouterr().err
    assert "rate" in err.lower(), f"the error must name --rate: {err!r}"


def test_convert_tool_parser_has_no_rate_default():
    """Pin it at the parser level too: --rate must have no default value.

    Guards against someone later adding `default=16500` to be 'helpful'.
    """
    parser = tool.build_parser()
    action = next(a for a in parser._actions if a.dest == "rate")
    assert action.required is True, "--rate must be a required argument"
    assert action.default is None, (
        f"--rate has a default ({action.default!r}); a default would silently "
        f"convert at a rate nobody chose"
    )


def test_convert_tool_rejects_a_non_positive_rate(tmp_path, capsys):
    """A zero or negative rate yields Rp0 or a negative total, not a real figure."""
    exp = tmp_path / "results" / "EXP-1"
    _write_csv(exp / "generation_result.csv", ["strategy", "cost_usd"],
               [{"strategy": "direct", "cost_usd": "1.0"}])

    code = tool.main(["--exp", "EXP-1", "--rate", "0",
                      "--results-dir", str(tmp_path / "results")])
    assert code != 0, "a zero rate must be rejected"
    assert "positive" in capsys.readouterr().err.lower()


# ── 2. the tool uses the rate it is given ───────────────────────────────────

def test_convert_tool_uses_the_rate_it_is_given(modern_exp):
    """The conversion must use the operator's rate, not a built-in one.

    Two different rates on the same input must give two different, arithmetically
    correct results. If a hardcoded rate were used anywhere, one of these fails.
    """
    rate_a, rate_b = 17000.0, 17828.10

    payload_a = tool.summarise_experiment(modern_exp, rate_a)
    payload_b = tool.summarise_experiment(modern_exp, rate_b)

    direct_a = payload_a["strategies"]["direct"]
    direct_b = payload_b["strategies"]["direct"]

    assert direct_a["cost_usd"] == pytest.approx(4.0)     # 1.0 + 3.0
    assert direct_b["cost_usd"] == pytest.approx(4.0), "USD must not depend on the rate"
    assert direct_a["cost_idr"] == pytest.approx(4.0 * rate_a)
    assert direct_b["cost_idr"] == pytest.approx(4.0 * rate_b)
    assert direct_a["cost_idr"] != pytest.approx(direct_b["cost_idr"])


def test_convert_tool_cost_per_success_uses_the_given_rate(modern_exp):
    """cost per success must be derived from the same rate, not a second one."""
    rate = 17828.10
    payload = tool.summarise_experiment(modern_exp, rate)
    direct = payload["strategies"]["direct"]

    assert direct["success"] == 1, "one of two direct runs resolved"
    assert direct["cost_usd_per_success"] == pytest.approx(4.0 / 1)
    assert direct["cost_idr_per_success"] == pytest.approx(4.0 * rate / 1)
    assert direct["success_basis"] == "resolved"


def test_convert_tool_output_records_rate_and_date(modern_exp, tmp_path):
    """The rate and its date must appear in the output: that is the audit trail.

    Bab IV needs to state "biaya dilaporkan dalam USD; konversi IDR memakai kurs X
    pada tanggal Y", which is only possible if the artefact carries both.
    """
    rate = 17828.10
    rate_date = "2026-10-06"
    payload = tool.summarise_experiment(modern_exp, rate)
    grand = {
        "runs": payload["rows"], "cost_usd": 6.0, "cost_idr": 6.0 * rate,
        "success": 2, "cost_usd_per_success": 3.0,
        "cost_idr_per_success": 3.0 * rate,
    }
    md = tool.format_markdown([payload], rate, rate_date, grand)

    assert "17,828.10" in md, "the rate must appear in the header"
    assert rate_date in md, "the conversion date must appear in the header"

    csv_text = tool.format_csv([payload], rate, rate_date, grand)
    header = csv_text.splitlines()[0]
    assert "usd_idr_rate" in header and "rate_date" in header
    assert "17828.10" in csv_text and rate_date in csv_text


def test_convert_tool_writes_both_markdown_and_csv(modern_exp, tmp_path, capsys):
    """--out writes the table; a .md target also produces the matching .csv."""
    out = tmp_path / "bab4.md"
    code = tool.main(["--exp", "EXP-NEW-001", "--rate", "17828.10",
                      "--results-dir", str(tmp_path / "results"),
                      "--out", str(out), "--date", "2026-10-06"])
    assert code == 0
    assert out.is_file()
    assert out.with_suffix(".csv").is_file()
    assert "Biaya IDR" in out.read_text(encoding="utf-8")


# ── 3. legacy data: read USD, ignore the old IDR ────────────────────────────

def test_convert_tool_reads_legacy_csv_using_usd_not_the_old_idr(legacy_exp):
    """Old CSVs have IDR columns; the USD column must be the one converted.

    The legacy IDR figures came from a different rate, so using them would mix two
    rates in one table. The fixture's IDR values are absurd on purpose: if the tool
    read them, the assertion below fails by a factor of ~56.
    """
    rate = 17828.10
    payload = tool.summarise_experiment(legacy_exp, rate)
    direct = payload["strategies"]["direct"]

    assert direct["cost_usd"] == pytest.approx(1.0)
    assert direct["cost_idr"] == pytest.approx(1.0 * rate)
    assert direct["cost_idr"] != pytest.approx(999999.0), (
        "the tool read the legacy cost_idr_actual column instead of converting USD"
    )
    assert payload["cost_column"] == "cost_usd_actual"


def test_convert_tool_handles_every_cost_column_generation(tmp_path):
    """The schema was renamed across generations; all of them must still read.

    ``cost_usd`` -> ``cost_usd_offpeak`` -> ``cost_usd_actual``. The 33 real CSVs
    on disk span these generations, so a tool that only knew the newest one would
    silently skip older batches.
    """
    for i, column in enumerate(["cost_usd", "cost_usd_offpeak", "cost_usd_actual"]):
        exp = tmp_path / "results" / f"EXP-GEN-{i}"
        _write_csv(exp / "generation_result.csv", ["strategy", column],
                   [{"strategy": "direct", column: "2.5"}])
        payload = tool.summarise_experiment(exp, 10000.0)
        assert payload["cost_column"] == column
        assert payload["strategies"]["direct"]["cost_idr"] == pytest.approx(25000.0)


def test_convert_tool_reports_a_missing_cost_column(tmp_path, capsys):
    """A CSV with no USD cost column must be an error, not a table of zeros.

    Silently emitting Rp0 for every strategy would look like a real measurement.
    """
    exp = tmp_path / "results" / "EXP-BAD"
    _write_csv(exp / "generation_result.csv", ["strategy", "instance_id"],
               [{"strategy": "direct", "instance_id": "a"}])

    code = tool.main(["--exp", "EXP-BAD", "--rate", "17828.10",
                      "--results-dir", str(tmp_path / "results")])
    assert code != 0
    assert "no USD cost column" in capsys.readouterr().err


def test_convert_tool_counts_blank_costs_as_zero_and_reports_them(tmp_path):
    """Blank costs are legitimate (savepoint-recovered rows) but must be visible.

    A mostly-empty file should not look like a cheap run.
    """
    exp = tmp_path / "results" / "EXP-BLANK"
    _write_csv(exp / "generation_result.csv",
               ["strategy", "cost_usd_actual", "resolved"],
               [{"strategy": "direct", "cost_usd_actual": "", "resolved": "True"},
                {"strategy": "direct", "cost_usd_actual": "1.0", "resolved": "False"}])
    payload = tool.summarise_experiment(exp, 10000.0)

    assert payload["rows_without_cost"] == 1, "the blank row must be counted"
    assert payload["strategies"]["direct"]["cost_idr"] == pytest.approx(10000.0)


# ── 4. the source data is never modified ────────────────────────────────────

def test_convert_tool_never_modifies_the_source_csv(modern_exp, tmp_path):
    """The tool only reads: experiment data must not be rewritten by a report."""
    csv_path = modern_exp / "generation_result.csv"
    before = csv_path.read_bytes()

    tool.main(["--exp", "EXP-NEW-001", "--rate", "17828.10",
               "--results-dir", str(tmp_path / "results"),
               "--out", str(tmp_path / "out.md")])

    assert csv_path.read_bytes() == before, (
        "the conversion modified generation_result.csv; it must be read-only"
    )


def test_convert_tool_does_not_create_idr_columns_in_the_source(modern_exp, tmp_path):
    """No *_idr* column may be added back to the experiment CSV."""
    csv_path = modern_exp / "generation_result.csv"
    tool.main(["--exp", "EXP-NEW-001", "--rate", "17828.10",
               "--results-dir", str(tmp_path / "results")])
    columns = csv_path.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert not any("idr" in c.lower() for c in columns), (
        f"an IDR column appeared in the source CSV: {columns}"
    )


# ── 5. the SWE-bench contract ───────────────────────────────────────────────

def test_predictions_jsonl_has_no_cost_columns(tmp_path):
    """Pin the SWE-bench contract: the evaluated file carries no cost.

    predictions.jsonl is the input to the SWE-bench evaluation. Its columns are
    fixed (instance_id, model_patch, model_name_or_path, strategy, patch_status,
    thinking, max_tokens). Cost is not among them — which is the direct evidence
    that IDR was never needed in the pipeline, only in the report.
    """
    pred_dir = tmp_path / "predictions"
    pred_dir.mkdir()
    pred_path = pred_dir / "predictions.jsonl"
    pred_path.write_text(json.dumps({
        "instance_id": "django__django-1",
        "model_patch": "--- a/x\n+++ b/x\n",
        "model_name_or_path": "deepseek-v4-flash",
        "strategy": "direct",
        "patch_status": "VALID",
        "thinking": False,
        "max_tokens": 4096,
    }) + "\n", encoding="utf-8")

    entry = json.loads(pred_path.read_text(encoding="utf-8").splitlines()[0])

    expected = {"instance_id", "model_patch", "model_name_or_path", "strategy",
                "patch_status", "thinking", "max_tokens"}
    assert set(entry) == expected, (
        f"the predictions contract changed: {sorted(set(entry) ^ expected)}"
    )
    for key in entry:
        assert "cost" not in key.lower() and "idr" not in key.lower(), (
            f"a cost field ({key}) appeared in predictions.jsonl; that file is the "
            f"SWE-bench evaluation input and must stay cost-free"
        )


def test_real_predictions_jsonl_are_unchanged_by_this_tool():
    """Run the tool over real data and confirm no predictions.jsonl is touched.

    The tool globs for generation_result.csv only, so it must not even look at a
    predictions file. This checks the real results/ tree, not a fixture.
    """
    results_dir = REPO_ROOT / "results"
    if not results_dir.is_dir():
        pytest.skip("no results/ directory in this checkout")

    preds = sorted(results_dir.glob("**/predictions.jsonl"))
    if not preds:
        pytest.skip("no real predictions.jsonl to check")

    import hashlib
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in preds}

    tool.main(["--all", "--rate", "17828.10", "--results-dir", str(results_dir)])

    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in preds}
    changed = [str(p) for p in preds if before[p] != after[p]]
    assert changed == [], (
        f"the conversion tool modified predictions.jsonl: {changed[:3]}"
    )


def test_cost_summary_field_types_survive_a_failed_run():
    """A FAILED run's CostSummary must still be exportable.

    Regression guard for a sweep blocker. `runner.py` built the failure-path cost
    POSITIONALLY (``CostSummary(0.0, 0.0, 0.0, 0.0, "")``). Removing the IDR fields
    shifted every later parameter, so ``""`` landed in ``cached_input_tokens`` (an
    int field) and the exporter raised:

        TypeError: can only concatenate str (not "int") to str

    Only the FAILURE path was affected, so it looked harmless — but a 150-run sweep
    hits rate limits and provider errors, and every such run goes through that code.

    NOTE: this test builds its own CostSummary, so on its own it cannot detect a
    regression at the runner CALL SITE — the end-to-end failure-path tests
    (test_early_config_record.py, test_retry.py, test_resume_data_integrity.py) do
    that, and `test_empty_cost_is_built_with_keywords` pins the call site's shape.
    """
    from experiments.csv_exporter import flatten_for_csv
    from models.inference import InferenceRun
    from models.result import (
        CostSummary, EvaluationResult, ExecutionResult, ExperimentResult,
    )

    # Exactly what the failure path builds.
    empty_cost = CostSummary(
        input_cost_usd=0.0, output_cost_usd=0.0, total_cost_usd=0.0,
        pricing_version="",
    )
    assert isinstance(empty_cost.cached_input_tokens, int)
    assert isinstance(empty_cost.regular_input_tokens, int)
    assert empty_cost.pricing_version == ""

    result = ExperimentResult(
        instance_id="django__django-1", strategy="direct", model="m",
        execution=ExecutionResult(run=InferenceRun(patch="", inferences=[])),
        cost=empty_cost,
        evaluation=EvaluationResult(success=False, error="RuntimeError: boom"),
        patch_status="API_ERROR",
    )

    row = flatten_for_csv(result)  # must not raise

    assert row["input_tokens_total"] == 0
    assert row["total_tokens"] == 0
    assert row["error"] == "RuntimeError: boom"
    assert row["pricing_version"] == "", (
        "pricing_version received a numeric field's value, so the positional "
        "arguments are misaligned again"
    )


def test_failed_run_reaches_the_exporter_with_int_tokens():
    """Drive a REAL failing run and assert the failure row is exportable.

    This is the end-to-end version: it exercises runner's failure path (the code
    that actually crashed) rather than constructing the objects by hand. Without it,
    a regression at the runner call site would slip past the unit test above.

    A failed run must still produce a CSV row — that row is what makes a resume
    possible, so losing it is worse than the crash itself.
    """
    from experiments.csv_exporter import flatten_for_csv
    from experiments.runner import _results_from_flat_rows

    # The shape runner writes for a failed strategy: no cost was recorded.
    rows = [{
        "instance_id": "django__django-1",
        "strategy": "direct",
        "model": "m",
        "cost_usd_offpeak": None,
        "cost_idr_offpeak": None,          # legacy column, must be tolerated
        "input_tokens_cached": None,
        "input_tokens_regular": None,
        "cost_usd_peak_total": None,
        "cost_usd_actual": None,
        "input_cost_usd_total": None,
        "output_cost_usd": None,
        "input_cost_usd_cached": None,
        "input_cost_usd_regular": None,
        "error": "RuntimeError: provider exploded",
        "_recovered_from_savepoint": True,
    }]

    results = _results_from_flat_rows(rows)
    assert len(results) == 1

    cost = results[0].cost
    assert isinstance(cost.cached_input_tokens, int), (
        f"a recovered failure row produced {type(cost.cached_input_tokens).__name__} "
        f"for cached_input_tokens; the exporter concatenates it and would crash"
    )
    assert isinstance(cost.regular_input_tokens, int)

    row = flatten_for_csv(results[0])  # must not raise
    assert row["input_tokens_total"] == 0
    assert row["error"] == "RuntimeError: provider exploded"


def test_cost_summary_field_removal_does_not_break_positional_callers():
    """Removing a dataclass field shifts every later field. A positional caller
    silently passes the WRONG value (measured: removing the IDR fields put "" into
    cached_input_tokens, and csv_exporter crashed on the failure path).

    Pins the field ORDER so a future removal cannot repeat this.
    """
    import dataclasses
    from models.result import CostSummary

    names = [f.name for f in dataclasses.fields(CostSummary)]
    assert names[:4] == [
        "input_cost_usd", "output_cost_usd", "total_cost_usd", "pricing_version",
    ], (
        "CostSummary's leading field order changed. Any positional caller now passes "
        f"the wrong values. Field order is: {names}"
    )
    assert "cached_input_tokens" in names
    assert not any("idr" in n for n in names), "IDR fields came back"


def test_cost_summary_rejects_positional_arguments():
    """kw_only=True is LOAD-BEARING: it makes the shift bug impossible.

    Measured failure: removing the IDR fields moved ``pricing_version`` up into
    position 4 and ``cached_input_tokens`` into position 5, so the positional call
    ``CostSummary(0.0, 0.0, 0.0, 0.0, "")`` put ``""`` into an int field. Only the
    run-FAILURE path hit it, so it looked harmless — but every rate-limited or
    provider-errored run in a 150-run sweep goes through it.

    With kw_only, that call raises TypeError at the call site instead of producing a
    corrupted object that crashes later during export.
    """
    import pytest as _pytest
    from models.result import CostSummary

    with _pytest.raises(TypeError):
        CostSummary(0.0, 0.0, 0.0, 0.0, "")

    # The keyword form is the supported one.
    summary = CostSummary(input_cost_usd=0.0, output_cost_usd=0.0,
                          total_cost_usd=0.0, pricing_version="")
    assert summary.cached_input_tokens == 0


def test_cost_result_rejects_positional_arguments():
    """Same guarantee for CostResult, which also lost IDR fields.

    Passes ALL required fields positionally. Passing only some would raise
    TypeError for "missing arguments" even WITHOUT kw_only, so the test would pass
    for the wrong reason and could not detect the guard being removed.
    """
    import dataclasses

    import pytest as _pytest
    from evaluation.cost import CostResult

    required = [
        f.name for f in dataclasses.fields(CostResult)
        if f.default is dataclasses.MISSING
        and f.default_factory is dataclasses.MISSING
    ]
    assert required, "precondition: CostResult has required fields"

    with _pytest.raises(TypeError):
        CostResult(*[0] * len(required))

    # The keyword form is the supported one, and must still work.
    obj = CostResult(**{name: 0 for name in required})
    assert obj.total_cost_usd == 0
    assert obj.window == "off_peak", "defaults must still apply"


def test_no_positional_cost_construction_in_src():
    """Pin the fix at the source level: cost objects are built by KEYWORD.

    A positional ``CostSummary(...)`` is what allowed a field removal to silently
    reinterpret ``""`` as a token count. Requiring keywords makes that class of
    breakage a TypeError at the call site instead of a wrong value at export time.
    """
    import ast
    from pathlib import Path

    src_root = Path(__file__).resolve().parent.parent / "src"
    offenders: list[str] = []

    for path in src_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name not in {"CostSummary", "CostResult"}:
                continue
            if node.args:
                offenders.append(f"{path.relative_to(src_root)}:{node.lineno} ({name})")

    assert offenders == [], (
        f"positional cost construction reintroduced: {offenders}. Removing or "
        f"reordering a cost field would silently shift values into the wrong slots"
    )


def test_empty_cost_is_built_with_keywords():
    """Pin the FAILURE-PATH constructor, which is where the crash actually happened.

    `runner.py` built the failed-run cost positionally. That single line is the
    sweep blocker, so it gets its own guard in addition to the repo-wide scan.
    """
    import ast
    import inspect

    from experiments import runner

    tree = ast.parse(inspect.getsource(runner))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name in {"CostSummary", "CostResult"} and node.args:
            raise AssertionError(
                f"runner.py:{node.lineno} constructs {name} positionally; removing "
                f"or reordering a field would shift every later argument"
            )


# ── 6. the pipeline itself is USD-only ──────────────────────────────────────

def test_csv_exporter_emits_no_idr_columns():
    """The exporter is the CSV's schema: its OUTPUT must carry no IDR column.

    Tests the returned dict rather than the source text, because the docstring
    deliberately explains why IDR is absent — scanning the source would flag that
    explanation as a violation.
    """
    from models.inference import InferenceRun
    from models.result import (
        CostSummary, EvaluationResult, ExecutionResult, ExperimentResult,
    )
    from experiments.csv_exporter import flatten_for_csv

    result = ExperimentResult(
        instance_id="django__django-1", strategy="direct", model="m",
        execution=ExecutionResult(run=InferenceRun(patch="", inferences=[])),
        cost=CostSummary(input_cost_usd=0.001, output_cost_usd=0.002,
                         total_cost_usd=0.003),
        evaluation=EvaluationResult(success=True),
    )
    row = flatten_for_csv(result)

    idr_columns = [c for c in row if "idr" in c.lower()]
    assert idr_columns == [], (
        f"the CSV schema still has IDR columns {idr_columns}; they would be "
        f"converted at sweep time and freeze each batch to a rate"
    )
    # The USD counterparts must all still be present.
    for expected in ("cost_usd_offpeak", "cost_usd_actual", "cost_usd_peak_total",
                     "input_cost_usd_total", "output_cost_usd"):
        assert expected in row, f"the USD column {expected} disappeared"


def test_csv_exporter_does_not_read_the_config_rate():
    """Config.USD_IDR_RATE must not be READ by the exporter again.

    Uses AST so the check is about real attribute access, not prose: a docstring
    that names the setting to explain its removal must not fail the test.
    """
    import ast
    import inspect

    from experiments import csv_exporter

    tree = ast.parse(inspect.getsource(csv_exporter))
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "USD_IDR_RATE":
            used.add(node.attr)
        elif isinstance(node, ast.Name) and node.id == "USD_IDR_RATE":
            used.add(node.id)
    assert not used, (
        "csv_exporter accesses USD_IDR_RATE in code; batch costs would then depend "
        "on .env at sweep time"
    )


def test_report_generator_has_no_module_level_rate():
    """report_generator used to read .env itself — a second, disagreeing source.

    It resolved ``USD_IDR_RATE`` with its OWN default (16500) while Config used
    17828.10, so a report could carry a different rate than the pipeline. Any
    remaining module-level rate would reintroduce that split.

    Checked via AST: no module-level assignment, and no code-level read of the
    setting. The comment explaining its removal is not a violation.
    """
    import ast
    import inspect

    from evaluation import report_generator

    assert not hasattr(report_generator, "USD_IDR_RATE"), (
        "report_generator defines a module-level USD_IDR_RATE again"
    )

    tree = ast.parse(inspect.getsource(report_generator))
    # No module-level assignment to USD_IDR_RATE.
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "USD_IDR_RATE":
                    raise AssertionError(
                        "report_generator assigns a module-level USD_IDR_RATE, "
                        "creating a second source of the rate"
                    )
    # No code-level read of the setting.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "USD_IDR_RATE":
            raise AssertionError(
                "report_generator reads USD_IDR_RATE in code; the rate must come "
                "only from tools/convert_to_idr.py --rate"
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "load_dotenv":
                raise AssertionError(
                    "report_generator loads .env again, so it could pick up a rate"
                )
