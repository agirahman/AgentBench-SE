"""Attribute each message to its role, to see WHO chose the wrong regex flag.

The gold patch for django-11001 uses `re.MULTILINE | re.DOTALL`, so MULTILINE
alone is insufficient (without DOTALL, `.` never spans newlines). review failed
with MULTILINE alone. The question that matters for the thesis is whether the
reviewer *changed* a working fix or merely *failed to catch* a bad one chosen
earlier — so print the real role of each message rather than guessing from length.
"""

import json
import sys
from pathlib import Path

ROOT = Path("D:/development/Skripsi2/AgantBech-SE")
out = Path(sys.argv[1])
mj = ROOT / "results" / "EXP-20260927-007" / "artifacts" / "django__django-11001" / "review" / "messages.jsonl"

_buf = []
def emit(s=""): _buf.append(s)

rows = []
for line in mj.read_text(encoding="utf-8").splitlines():
    if line.strip():
        rows.append(json.loads(line))

emit(f"messages: {len(rows)}")
emit()
emit("keys of message[1]:")
for k, v in rows[1].items():
    vs = json.dumps(v)[:120] if not isinstance(v, str) else v[:120]
    emit(f"  {k}: {vs}")
emit()

for i, r in enumerate(rows):
    # Print every key that could hold a role label
    labels = {k: r.get(k) for k in ("role", "agent", "name", "author", "source", "type") if k in r}
    content = r.get("content")
    if not isinstance(content, str):
        content = json.dumps(content) if content is not None else ""
    flag = ""
    if "re.DOTALL" in content:
        flag = "DOTALL"
    if "re.MULTILINE" in content:
        flag = (flag + "+MULTILINE") if flag else "MULTILINE"
    emit(f"[{i}] labels={labels} len={len(content)} flag={flag or '-'}")

out.write_text("\n".join(_buf) + "\n", encoding="utf-8")
print("ok")
