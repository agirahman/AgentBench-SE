#!/usr/bin/env python
"""
Modal Cloud evaluation wrapper untuk SWE-bench predictions.

Usage:
    python tools/eval_modal.py results/EXP-20260717-009/predictions/direct.jsonl
    python tools/eval_modal.py results/EXP-20260717-009/predictions/direct.jsonl --force

run_id dihasilkan otomatis: modal-<strategy>-<EXP-YYYYMMDD-NNN> (taut ke hasil phase 1).
Gunakan --force untuk mengevaluasi ulang (default: skip jika *_results.json sudah ada).
"""

import os
import sys

# Windows: re-exec in UTF-8 mode so swebench's log writes (cp1252 default) don't crash
# on unicode test output (e.g. box-drawing chars from pytest). PEP 540.
if __name__ == "__main__" and sys.platform == "win32" and os.environ.get("PYTHONUTF8") != "1":
    os.environ["PYTHONUTF8"] = "1"
    os.execv(sys.executable, [sys.executable, *sys.argv])

import io
import json
from pathlib import Path
from typing import Optional

# Fix Windows console encoding for Unicode (Modal rich output).
# Only applied when running as a script so imports (e.g. from tests) are side-effect free.
if sys.platform == "win32" and __name__ == "__main__":
    import types
    resource = types.ModuleType("resource")
    setattr(resource, "getrlimit", lambda *_: (0, 0))
    setattr(resource, "setrlimit", lambda *_: None)   # jaga-jaga kalau dipanggil juga
    setattr(resource, "RLIMIT_NOFILE", 0)
    sys.modules["resource"] = resource
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from swebench.harness.modal_eval import run_instances_modal, validate_modal_credentials
from swebench.harness.utils import get_predictions_from_file, load_swebench_dataset
from swebench.harness.constants import KEY_INSTANCE_ID, KEY_MODEL


def build_run_id(predictions_path: Path, exp_id: Optional[str] = None) -> str:
    """Build a unique Modal run_id linking phase 2 (eval) to phase 1 (experiment).

    Format: ``modal-<strategy>-<EXP-YYYYMMDD-NNN>`` (e.g. modal-direct-EXP-20260819-002)
    so eval results and phase-1 experiment results share the same EXP id.

    The experiment id is derived from the predictions folder layout
    ``results/<EXP-ID>/predictions/<strategy>.jsonl``. ``exp_id`` overrides it.
    """
    strategy = predictions_path.stem
    if exp_id is None:
        exp_dir = predictions_path.parent.parent.name
        exp_id = exp_dir if exp_dir.startswith("EXP-") else ""
    if exp_id:
        return f"modal-{strategy}-{exp_id}"
    return f"modal-{strategy}"


def extract_failure_reason(log_path: Path) -> Optional[str]:
    """Extract a human-readable failure reason from a run_instance.log."""
    if not Path(log_path).exists():
        return None

    text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "Patch Apply Failed" in line:
            detail_lines = []
            for candidate in lines[i + 1:i + 6]:
                candidate = candidate.strip()
                if not candidate or "Traceback" in candidate:
                    continue
                if candidate.startswith(("File ", "swebench.")):
                    continue
                detail_lines.append(candidate)
            detail = " | ".join(detail_lines) if detail_lines else "unknown"
            return f"APPLY_PATCH_FAIL: {detail}"
        if "Test runtime" in line and ("timeout" in line.lower() or ">" in line):
            return "TESTS_TIMEOUT"
    return None


def load_instance_report(log_dir: Path, inst_id: str) -> dict:
    """Read report.json for one instance, keyed by instance_id."""
    report_path = Path(log_dir) / inst_id / "report.json"
    if not report_path.exists():
        return {}
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data.get(inst_id, {})


def enrich_instance_result(inst_id: str, resolved: bool, log_dir: Optional[Path]) -> dict:
    """Build a result dict with patch_applied and failure_reason."""
    report = load_instance_report(log_dir, inst_id) if log_dir else {}
    patch_applied = bool(report.get("patch_successfully_applied", resolved))
    failure_reason = None
    if not resolved:
        if log_dir:
            failure_reason = extract_failure_reason(Path(log_dir) / inst_id / "run_instance.log")
        if failure_reason is None:
            if not report:
                failure_reason = "no report"
            elif not report.get("patch_successfully_applied", False):
                failure_reason = "APPLY_PATCH_FAIL"
            else:
                tests_status = report.get("tests_status") or {}
                failed = [
                    test
                    for key in ("FAIL_TO_PASS", "PASS_TO_PASS")
                    for test in (tests_status.get(key) or {}).get("failure", [])
                ]
                if failed:
                    failure_reason = "TESTS_ERROR"
                else:
                    failure_reason = "unresolved"
    return {
        "instance_id": inst_id,
        "resolved": bool(resolved),
        "patch_applied": patch_applied,
        "failure_reason": failure_reason,
    }


def classify_summary(
    summary: dict,
    predictions_list: list,
    log_dir: Optional[Path],
) -> tuple[list, int, int, int, int]:
    """Turn a Modal harness summary into per-instance rows plus counters.

    Returns ``(results, resolved_count, total_count, empty_count, error_count)``.

    Kept as a pure function (no Modal, no filesystem beyond the optional log_dir)
    because the arithmetic here decides the headline number of the thesis, and it
    was previously inlined in ``main()`` where no test could reach it -- which is
    how an empty patch came to be counted as a strategy failure.

    An empty patch is not a wrong patch. The official harness separates them
    (``reporting.py``: `empty_patch_ids`, "Instances with empty patches"), and it
    also drops them from the container run entirely (``run_evaluation.py:458``).
    A run that died at the provider -- EXP-20260929-022 django-11019/review, a
    502 -- must therefore not be averaged in as if the strategy had answered
    wrongly.
    """
    resolved_ids = set(summary.get("resolved_ids", []))
    error_ids = set(summary.get("error_ids", []))

    # Deduplicate by instance id, keeping the LAST row, before counting anything.
    #
    # A retried instance appends a second row to the savepoint, so the file can
    # hold the same instance twice. The harness deduplicates before evaluating
    # (it builds a dict), but this function iterated the raw list -- so a retried
    # instance incremented BOTH resolved_count and the number of rows while
    # total_count came from the harness's deduplicated count. Two populations,
    # one ratio: measured at 150% for one duplicate and 200% for a single-instance
    # file. The headline number of the thesis could exceed 100%.
    #
    # Last row wins because that is the newest attempt, matching _merge_csv_rows
    # in the runner and the harness's own dict build.
    deduped: dict = {}
    for pred in predictions_list:
        deduped[pred[KEY_INSTANCE_ID]] = pred
    predictions_list = list(deduped.values())

    # Prefer the harness's own submitted count; fall back to what we sent.
    total_count = summary.get("submitted_instances") or len(predictions_list)

    # The harness is authoritative about emptiness; re-derive only if it did not
    # say, so the classification still works on an older summary schema.
    empty_ids = set(summary.get("empty_patch_ids", []))
    if not empty_ids:
        empty_ids = {
            p[KEY_INSTANCE_ID] for p in predictions_list
            if not (p.get("model_patch") or "").strip()
        }

    results: list = []
    resolved_count = 0
    for pred in predictions_list:
        inst_id = pred[KEY_INSTANCE_ID]
        if inst_id in resolved_ids:
            results.append(enrich_instance_result(inst_id, True, log_dir))
            resolved_count += 1
        elif inst_id in empty_ids:
            row = enrich_instance_result(inst_id, False, log_dir)
            # Do not let a stale report.json claim this patch was applied: there
            # was no patch. The harness applies an empty diff as a no-op and its
            # report then says applied=True, which is true but misleading.
            row["patch_applied"] = False
            row["failure_reason"] = "EMPTY_PATCH"
            results.append(row)
        else:
            results.append(enrich_instance_result(inst_id, False, log_dir))

    # Arithmetic guard. resolved_count can never exceed the number of graded
    # instances, so if it does, the classification is wrong and the headline rate
    # is nonsense. Failing loudly here is better than emitting a rate above 100%
    # that a reader might quote: the duplicate-row bug produced exactly that and
    # was only caught by inspecting the output by hand.
    #
    # Returned as a flag rather than raised. A raise here happens AFTER Modal has
    # already run and been paid for, and a stale summary (the harness reuses a run
    # id, or resolved_ids carries an id from an earlier submission) would throw
    # away the whole evaluation over a bookkeeping mismatch. The per-instance rows
    # are still correct; only the ratio is suspect, so the ratio is what gets
    # withheld -- see main().
    rate_is_valid = resolved_count <= total_count
    if not rate_is_valid:
        print(
            f"  ERROR: resolved={resolved_count} exceeds total={total_count}. "
            f"An instance was counted more than once, so the success rate is NOT "
            f"meaningful and must not be quoted. Per-instance rows are still "
            f"written; check the predictions file for duplicate instance ids."
        )

    return {
        "results": results,
        "resolved": resolved_count,
        "total": total_count,
        "empty": len(empty_ids),
        "errors": len(error_ids),
        "rate_is_valid": rate_is_valid,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Modal Cloud evaluation wrapper untuk SWE-bench predictions."
    )
    parser.add_argument("predictions", type=Path, help="Path ke predictions/<strategy>.jsonl")
    parser.add_argument(
        "--exp-id",
        default=None,
        help="Override eksperimen id pada run_id (default: dari nama folder EXP-* parent predictions).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Timpa <strategy>_results.json yang sudah ada (default: skip jika sudah ada).",
    )
    args = parser.parse_args()

    predictions_path = args.predictions
    if not predictions_path.exists():
        print(f"Error: {predictions_path} not found")
        sys.exit(1)

    run_id = build_run_id(predictions_path, exp_id=args.exp_id)

    output_file = predictions_path.parent / f"{predictions_path.stem}_results.json"
    if output_file.exists() and not args.force:
        print(
            f"[SKIP] {output_file} sudah ada — jalankan ulang dengan --force "
            f"jika ingin mengevaluasi ulang (run_id: {run_id})."
        )
        sys.exit(0)

    # Validate Modal credentials
    try:
        validate_modal_credentials()
    except Exception as e:
        print(f"Error: Modal credentials not valid: {e}")
        print("Run: modal token new")
        sys.exit(1)

    # Load predictions (list of dicts)
    print(f"Loading predictions from {predictions_path}...")
    predictions_list = get_predictions_from_file(
        str(predictions_path),
        dataset_name="SWE-bench/SWE-bench_Lite",
        split="test",
    )
    print(f"Loaded {len(predictions_list)} predictions")

    # Convert list to dict for run_instances_modal
    predictions_dict = {
        pred[KEY_INSTANCE_ID]: pred
        for pred in predictions_list
    }

    # Load full dataset
    print("Loading SWE-bench Lite dataset...")
    full_dataset = load_swebench_dataset(
        name="SWE-bench/SWE-bench_Lite",
        split="test",
    )

    # Get instance IDs from predictions
    instance_ids = list(predictions_dict.keys())

    # Filter dataset for our instances
    instances = [inst for inst in full_dataset if inst[KEY_INSTANCE_ID] in instance_ids]
    print(f"Matched {len(instances)} instances from dataset")

    # Run evaluation
    print(f"Running Modal evaluation ({len(instances)} instances)...")
    print(f"View at: https://modal.com/apps/agirahman/main")
    run_instances_modal(
        predictions=predictions_dict,
        instances=instances,
        full_dataset=full_dataset,
        run_id=run_id,
        timeout=1800,  # 30 min per instance
    )

    # Parse results from Modal summary report file
    # Modal writes "{model_name}.{run_id}.json" in CWD
    model_name = predictions_list[0].get(KEY_MODEL, "model").replace("/", "__").replace(":", "_")
    summary_files = list(Path(".").glob(f"{model_name}.{run_id}.json"))

    resolved_count = 0
    results = []
    from swebench.harness.constants import RUN_EVALUATION_LOG_DIR
    log_dir = Path(RUN_EVALUATION_LOG_DIR) / run_id / model_name

    # An empty patch is not a wrong patch: the run died before producing one
    # (provider 502 in EXP-20260929-022 django-11019/review). The official
    # harness excludes these from the denominator and reports them in their own
    # bucket -- reporting.py builds `empty_patch_ids` and prints "Instances with
    # empty patches" separately. We must do the same, or a dead run silently
    # counts as a strategy failure and drags the resolved rate down.
    empty_count = 0
    error_count = 0

    if summary_files:
        summary_file = summary_files[-1]
        print(f"Reading summary from {summary_file}")
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
        classified = classify_summary(summary, predictions_list, log_dir)
        results = classified["results"]
        resolved_count = classified["resolved"]
        total_count = classified["total"]
        empty_count = classified["empty"]
        error_count = classified["errors"]
        rate_is_valid = classified["rate_is_valid"]
    else:
        # Fallback: look for report.json in local logs (if Modal synced them)
        total_count = 0
        for pred in predictions_list:
            inst_id = pred[KEY_INSTANCE_ID]
            total_count += 1
            report = load_instance_report(log_dir, inst_id)
            resolved = bool(report.get("resolved", False))
            results.append(enrich_instance_result(inst_id, resolved, log_dir))
            if resolved:
                resolved_count += 1
        rate_is_valid = True

    # Rate over SUBMITTED instances -- the conservative headline, comparable
    # across levels, and the same convention the SWE-bench paper uses. An empty
    # patch counts as not resolved here, so a level cannot look better just
    # because some of its runs died before producing anything.
    success_rate = (resolved_count / total_count * 100) if total_count > 0 else 0

    # Rate over instances that were actually GRADED. Useful on its own, but
    # never as the headline: review would read 100% here off two runs while
    # direct reads 67% off three, which would flatter review for having LOST a
    # data point to a provider outage. Both are reported so the reader can see
    # exactly which it is.
    graded_count = total_count - empty_count - error_count
    success_rate_graded = (resolved_count / graded_count * 100) if graded_count > 0 else 0

    # Print summary
    print("\n" + "=" * 60)
    print(f"Evaluation Complete: {predictions_path.name}")
    print("=" * 60)
    print(f"Total submitted: {total_count}")
    print(f"Resolved: {resolved_count}")
    print(f"Unresolved: {total_count - resolved_count - empty_count - error_count}")
    if empty_count:
        print(f"Empty patches: {empty_count}  <-- run died, NOT a wrong patch")
    if error_count:
        print(f"Harness errors: {error_count}")
    if rate_is_valid:
        print(f"Success rate: {success_rate:.1f}%  (resolved/submitted)")
        if graded_count != total_count:
            print(f"              {success_rate_graded:.1f}%  (resolved/graded, "
                  f"n={graded_count}; excludes empty patches and errors)")
    else:
        print("Success rate: WITHHELD -- resolved exceeds submitted, so the")
        print("              classification double-counted an instance. The")
        print("              per-instance rows below are still valid.")
    print("=" * 60)

    # Save results
    with open(output_file, "w") as f:
        json.dump({
            "predictions_file": str(predictions_path),
            "run_id": run_id,
            "total": total_count,
            "graded": graded_count,
            "empty_patches": empty_count,
            "harness_errors": error_count,
            "resolved": resolved_count,
            "unresolved": total_count - resolved_count - empty_count - error_count,
            # Null rather than a wrong number: a rate above 100% must never be
            # written where a reader could pick it up.
            "success_rate": success_rate if rate_is_valid else None,
            "success_rate_graded": success_rate_graded if rate_is_valid else None,
            "success_rate_is_valid": rate_is_valid,
            "results": results,
        }, f, indent=2, default=str)
    print(f"Results saved to {output_file}")


if __name__ == "__main__":
    main()
