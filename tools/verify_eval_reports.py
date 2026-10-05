"""Verify the Modal eval verdicts from the harness's own report.json files.

`*_results.json` is produced by our wrapper and could in principle be wrong; the
report.json under logs/run_evaluation is written by the SWE-bench harness itself
and records which graded tests passed. Checking it confirms that "resolved" came
from tests actually running, not from a bookkeeping bug on our side.
"""

import json
from pathlib import Path

RUNS = {
    "direct": "modal-direct-EXP-20260927-006",
    "planning": "modal-planning-EXP-20260927-006",
    "review": "modal-review-EXP-20260927-006",
}
MODEL = "oc__space-bunny-free"
INSTANCE = "django__django-10914"

base = Path("logs/run_evaluation")

for strategy, run_id in RUNS.items():
    rp = base / run_id / MODEL / INSTANCE / "report.json"
    print(f"=== {strategy} ({run_id}) ===")
    if not rp.exists():
        print(f"  report.json NOT FOUND at {rp}")
        continue
    data = json.loads(rp.read_text(encoding="utf-8"))
    report = data.get(INSTANCE, {})
    print(f"  resolved                  : {report.get('resolved')}")
    print(f"  patch_successfully_applied: {report.get('patch_successfully_applied')}")
    tests = report.get("tests_status") or {}
    for key in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        v = tests.get(key) or {}
        n_ok = len(v.get("success", []) or [])
        n_bad = len(v.get("failure", []) or [])
        print(f"  {key:14}: success={n_ok}  failure={n_bad}")
    f2p_bad = ((tests.get("FAIL_TO_PASS") or {}).get("failure") or [])
    if f2p_bad:
        print("  FAIL_TO_PASS still failing (first 3):")
        for t in f2p_bad[:3]:
            print(f"    {t}")
    print()
