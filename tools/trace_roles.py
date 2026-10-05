"""Full sender/receiver trace for the django-11001 review run.

Roles live in `sender`/`receiver`/`kind`, not `role`. This prints the whole chain
so the wrong-flag decision can be attributed to a specific agent, and shows the
planner's full plan plus the reviewer's verdict text.
"""

import json
import sys
from pathlib import Path

ROOT = Path("D:/development/Skripsi2/AgantBech-SE")
out = Path(sys.argv[1])

_buf = []
def emit(s=""): _buf.append(s)

for iid, strat in (("django__django-10924", "review"), ("django__django-10924", "planning")):
    d = ROOT / "results" / "EXP-20260927-007" / "artifacts" / iid / strat
    mj = d / "messages.jsonl"
    emit(f"########## {iid} / {strat} ##########")
    if not mj.exists():
        emit("  (missing)")
        emit()
        continue
    rows = [json.loads(l) for l in mj.read_text(encoding="utf-8").splitlines() if l.strip()]
    for i, r in enumerate(rows):
        sender = r.get("sender", "?")
        receiver = r.get("receiver", "?")
        kind = r.get("kind", "?")
        content = r.get("content") or ""
        if not isinstance(content, str):
            content = json.dumps(content)
        emit(f"  [{i}] {sender} -> {receiver}  kind={kind}  len={len(content)}")
    emit()

    # Planner plan (first planner->orchestrator result)
    for i, r in enumerate(rows):
        if r.get("sender") == "planner" and r.get("kind") == "result":
            content = r.get("content") or ""
            emit(f"  ---- planner result [{i}] ----")
            try:
                p = json.loads(content)
                for k, v in p.items():
                    vs = json.dumps(v) if not isinstance(v, str) else v
                    emit(f"    {k}: {vs[:300]}")
            except json.JSONDecodeError:
                emit(f"    {content[:600]}")
            emit()

    # Reviewer result
    for i, r in enumerate(rows):
        if r.get("sender") == "reviewer" and r.get("kind") == "result":
            content = r.get("content") or ""
            emit(f"  ---- reviewer result [{i}] ----")
            try:
                p = json.loads(content)
                for k, v in p.items():
                    vs = json.dumps(v) if not isinstance(v, str) else v
                    emit(f"    {k}: {vs[:400]}")
            except json.JSONDecodeError:
                emit(f"    {content[:600]}")
            emit()

out.write_text("\n".join(_buf) + "\n", encoding="utf-8")
print("ok")
