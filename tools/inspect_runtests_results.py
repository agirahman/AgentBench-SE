"""What run_tests actually returned, per agent, using the real key name.

tool_calls.jsonl stores {"agent", "tool", "arguments", "result_preview"} — not
"name"/"result". An earlier probe used the wrong keys and printed empty results,
which would have been read as "the tool returned nothing".
"""

import json
import sys
from pathlib import Path

ROOT = Path("D:/development/Skripsi2/AgantBech-SE")
out = Path(sys.argv[1])
art = ROOT / "results" / "EXP-20260927-007" / "artifacts"

_buf = []
def emit(s=""): _buf.append(s)

for iid in ("django__django-10914", "django__django-10924", "django__django-11001"):
    for s in ("direct", "planning", "review"):
        tc = art / iid / s / "tool_calls.jsonl"
        if not tc.exists():
            continue
        rows = [json.loads(l) for l in tc.read_text(encoding="utf-8").splitlines() if l.strip()]
        hits = [r for r in rows if r.get("tool") == "run_tests"]
        if not hits:
            continue
        emit(f"### {iid} / {s}")
        for r in hits:
            emit(f"  agent={r.get('agent')}  cmd={str(r.get('arguments',{}).get('command',''))[:130]}")
            prev = (r.get("result_preview") or "").replace("\n", " | ")
            emit(f"    -> {prev[:300]}")
        emit()

# Also: who edits what, per agent (edit_file counts)
emit("=== edit_file calls per agent ===")
for iid in ("django__django-11001", "django__django-10924"):
    for s in ("planning", "review"):
        tc = art / iid / s / "tool_calls.jsonl"
        if not tc.exists():
            continue
        rows = [json.loads(l) for l in tc.read_text(encoding="utf-8").splitlines() if l.strip()]
        from collections import Counter
        edits = Counter(r.get("agent") for r in rows if r.get("tool") == "edit_file")
        if edits:
            emit(f"  {iid} / {s}: {dict(edits)}")

out.write_text("\n".join(_buf) + "\n", encoding="utf-8")
print("ok")
