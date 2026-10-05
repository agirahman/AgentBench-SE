"""Inspect why instances failed: which graded tests broke, per strategy.

TESTS_ERROR means the patch applied but graded tests failed. For each failing
(instance, strategy) pair this prints the FAIL_TO_PASS / PASS_TO_PASS counts and
the first few failing test names, from the harness's own report.json. This
separates "the patch was wrong" from "the harness recorded something odd".
"""

import json
import sys
from pathlib import Path

ROOT = Path("D:/development/Skripsi2/AgantBech-SE")
out = Path(sys.argv[1])
base = ROOT / "logs" / "run_evaluation"
MODEL = "oc__space-bunny-free"

runs = {
    "direct": "modal-direct-EXP-20260927-007",
    "planning": "modal-planning-EXP-20260927-007",
    "review": "modal-review-EXP-20260927-007",
}

_buf = []
def emit(s=""): _buf.append(s)

for strategy, run_id in runs.items():
    emit(f"=== {strategy} ({run_id}) ===")
    rundir = base / run_id / MODEL
    if not rundir.exists():
        emit(f"  dir not found: {rundir}")
        emit()
        continue
    for instdir in sorted(rundir.iterdir()):
        rp = instdir / "report.json"
        if not rp.exists():
            continue
        data = json.loads(rp.read_text(encoding="utf-8"))
        report = data.get(instdir.name, {})
        resolved = report.get("resolved")
        applied = report.get("patch_successfully_applied")
        tests = report.get("tests_status") or {}
        f2p = tests.get("FAIL_TO_PASS") or {}
        p2p = tests.get("PASS_TO_PASS") or {}
        nf2p_ok = len(f2p.get("success") or [])
        nf2p_bad = len(f2p.get("failure") or [])
        np2p_ok = len(p2p.get("success") or [])
        np2p_bad = len(p2p.get("failure") or [])
        emit(f"  {instdir.name:26} resolved={str(resolved):5} applied={str(applied):5} "
             f"F2P ok/bad={nf2p_ok}/{nf2p_bad}  P2P ok/bad={np2p_ok}/{np2p_bad}")
        if nf2p_bad:
            emit("    F2P failures:")
            for t in (f2p.get("failure") or [])[:4]:
                emit(f"      {t[:110]}")
        if np2p_bad:
            emit("    P2P failures (first 4):")
            for t in (p2p.get("failure") or [])[:4]:
                emit(f"      {t[:110]}")
    emit()

out.write_text("\n".join(_buf) + "\n", encoding="utf-8")
print("ok")
