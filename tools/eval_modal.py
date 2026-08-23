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

    if summary_files:
        summary_file = summary_files[-1]
        print(f"Reading summary from {summary_file}")
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
        resolved_ids = set(summary.get("resolved_ids", []))
        error_ids = set(summary.get("error_ids", []))
        unresolved_ids = set(summary.get("unresolved_ids", []))
        total_count = summary.get("submitted_instances", 0)

        for pred in predictions_list:
            inst_id = pred[KEY_INSTANCE_ID]
            if inst_id in resolved_ids:
                results.append(enrich_instance_result(inst_id, True, log_dir))
                resolved_count += 1
            elif inst_id in error_ids:
                results.append(enrich_instance_result(inst_id, False, log_dir))
            else:
                results.append(enrich_instance_result(inst_id, False, log_dir))
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

    success_rate = (resolved_count / total_count * 100) if total_count > 0 else 0

    # Print summary
    print("\n" + "=" * 60)
    print(f"Evaluation Complete: {predictions_path.name}")
    print("=" * 60)
    print(f"Total instances: {total_count}")
    print(f"Resolved: {resolved_count}")
    print(f"Unresolved: {total_count - resolved_count}")
    print(f"Success rate: {success_rate:.1f}%")
    print("=" * 60)

    # Save results
    with open(output_file, "w") as f:
        json.dump({
            "predictions_file": str(predictions_path),
            "run_id": run_id,
            "total": total_count,
            "resolved": resolved_count,
            "unresolved": total_count - resolved_count,
            "success_rate": success_rate,
            "results": results,
        }, f, indent=2, default=str)
    print(f"Results saved to {output_file}")


if __name__ == "__main__":
    main()
