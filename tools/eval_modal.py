#!/usr/bin/env python
"""
Modal Cloud evaluation wrapper untuk SWE-bench predictions.

Usage:
    python tools/eval_modal.py results/EXP-20260717-009/predictions/direct.jsonl
"""

import io
import json
import sys
from pathlib import Path

# Fix Windows console encoding for Unicode (Modal rich output)
if sys.platform == "win32":
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
from swebench.harness.constants import KEY_INSTANCE_ID


def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/eval_modal.py <predictions_jsonl_path>")
        sys.exit(1)

    predictions_path = Path(sys.argv[1])
    if not predictions_path.exists():
        print(f"Error: {predictions_path} not found")
        sys.exit(1)

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
        dataset_name="princeton-nlp/SWE-bench_Lite",
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
        name="princeton-nlp/SWE-bench_Lite",
        split="test",
    )

    # Get instance IDs from predictions
    instance_ids = list(predictions_dict.keys())

    # Filter dataset for our instances
    instances = [inst for inst in full_dataset if inst[KEY_INSTANCE_ID] in instance_ids]
    print(f"Matched {len(instances)} instances from dataset")

    # Generate run ID
    run_id = f"modal-{predictions_path.stem}"

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
    # Modal writes "deepseek-v4-flash.modal-direct.json" locally
    summary_files = list(Path(".").glob("deepseek-v4-flash.modal-*.json"))
    if not summary_files:
        summary_files = list(Path(".").glob("*.modal-*.json"))

    resolved_count = 0
    results = []
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
                results.append({"instance_id": inst_id, "resolved": True})
                resolved_count += 1
            elif inst_id in error_ids:
                results.append({"instance_id": inst_id, "resolved": False, "error": "eval error"})
            else:
                results.append({"instance_id": inst_id, "resolved": False})
    else:
        # Fallback: look for report.json in local logs (if Modal synced them)
        from swebench.harness.constants import RUN_EVALUATION_LOG_DIR
        model_name = predictions_list[0].get("model_name_or_path", "None").replace("/", "__")
        log_dir = Path(RUN_EVALUATION_LOG_DIR) / run_id / model_name

        total_count = 0
        for pred in predictions_list:
            inst_id = pred[KEY_INSTANCE_ID]
            total_count += 1
            report_path = log_dir / inst_id / "report.json"
            if report_path.exists():
                report = json.loads(report_path.read_text())
                resolved = report.get(inst_id, {}).get("resolved", False)
                results.append({"instance_id": inst_id, "resolved": resolved})
                if resolved:
                    resolved_count += 1
            else:
                results.append({"instance_id": inst_id, "resolved": False, "error": "report not found"})

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
    output_file = predictions_path.parent / f"{predictions_path.stem}_results.json"
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
