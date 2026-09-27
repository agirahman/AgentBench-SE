"""Did the reviewer have a chance to catch the planner's wrong flag?

django-11001's planner chose re.MULTILINE (wrong); the correct flag is re.DOTALL
(direct and planning used it and resolved). The reviewer approved MULTILINE. This
script checks whether the reviewer actually verified the fix (ran tests, read the
target file) or merely approved the plan's reasoning — which decides whether this
is "review couldn't have known" or "review rubber-stamped a wrong claim".

Prints the reviewer's own text, the per-role tool usage, and whether run_tests
was invoked at all in the review run.
"""

import json
import sys
from pathlib import Path

ROOT = Path("D:/development/Skripsi2/AgantBech-SE")
out = Path(sys.argv[1])
art = ROOT / "results" / "EXP-20260927-007" / "artifacts"

_buf = []
def emit(s=""): _buf.append(s)

INSTANCES = ["django__django-11001", "django__django-10924"]

for iid in INSTANCES:
    emit(f"########## {iid} ##########")
    emit()

    # Per-strategy tool usage from tool_calls.jsonl
    for s in ("direct", "planning", "review"):
        d = art / iid / s
        tc = d / "tool_calls.jsonl"
        emit(f"===== {s} =====")
        if tc.exists():
            names = []
            for line in tc.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                nm = r.get("tool") or r.get("name") or "?"
                names.append(nm)
            from collections import Counter
            c = Counter(names)
            emit(f"  tool calls: {len(names)}  {dict(c)}")
            emit(f"  run_tests used: {'run_tests' in c}")
        else:
            emit("  (no tool_calls.jsonl)")

        # summary.json
        sj = d / "summary.json"
        if sj.exists():
            try:
                sm = json.loads(sj.read_text(encoding="utf-8"))
                emit(f"  summary: {json.dumps(sm)[:400]}")
            except json.JSONDecodeError:
                pass
        emit()

    # Reviewer's full text
    rv = art / iid / "review" / "reviewer.md"
    if rv.exists():
        emit(f"--- reviewer.md ({iid}) ---")
        for ln in rv.read_text(encoding="utf-8").splitlines():
            emit(f"  {ln[:160]}")
        emit()

out.write_text("\n".join(_buf) + "\n", encoding="utf-8")
print("ok")
