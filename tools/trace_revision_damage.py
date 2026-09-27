"""Confirm the revision damaged the patch on django-10924.

The chain for review/django-10924 is:
  [1] planner plan, [4] executor result #1, [7] reviewer NEEDS_REVISION,
  [9] revision task, [10] executor result #2
If the reviewer asked for simplification and the executor's SECOND patch is the
one that failed while planning's single patch resolved, then review's revision
step is what broke it. This prints both executor results and compares the two
patches' core change.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path("D:/development/Skripsi2/AgantBech-SE")
out = Path(sys.argv[1])
exp = ROOT / "results" / "EXP-20260927-007"

_buf = []
def emit(s=""): _buf.append(s)

iid = "django__django-10924"
d = exp / "artifacts" / iid / "review"
rows = [json.loads(l) for l in (d / "messages.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]

emit("=== review message chain ===")
for i, r in enumerate(rows):
    content = r.get("content") or ""
    if not isinstance(content, str):
        content = json.dumps(content)
    emit(f"  [{i}] {r.get('sender')} -> {r.get('receiver')} kind={r.get('kind')} len={len(content)}")
emit()

for i in (4, 10):
    if i < len(rows):
        r = rows[i]
        emit(f"--- executor result [{i}] ---")
        content = r.get("content") or ""
        if not isinstance(content, str):
            content = json.dumps(content)
        try:
            p = json.loads(content)
            for k, v in p.items():
                vs = json.dumps(v) if not isinstance(v, str) else v
                emit(f"    {k}: {vs[:400]}")
        except json.JSONDecodeError:
            emit(f"    {content[:700]}")
        emit()

# Compare the deconstruct/get_prep_value lines across the three patches
emit("=== core change per strategy (fields/__init__.py) ===")
for s in ("planning", "review"):
    pf = exp / "patches" / f"{iid}_{s}.txt"
    text = pf.read_text(encoding="utf-8")
    emit(f"  --- {s} ({len(text)} bytes) ---")
    for ln in text.splitlines():
        if ln.startswith(("@@", "+", "-")) and not ln.startswith(("+++", "---")):
            if any(k in ln for k in ("path", "callable", "deconstruct", "get_prep")):
                emit(f"    {ln[:140]}")
    emit()

out.write_text("\n".join(_buf) + "\n", encoding="utf-8")
print("ok")
