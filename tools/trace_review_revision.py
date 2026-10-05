"""Did the reviewer damage a correct patch? Trace the revision flow.

For django-11001 the executor's fix is one line and the correct flag is
re.DOTALL (direct and planning, which both resolved, used it). Review instead
landed re.MULTILINE and failed. If the executor originally produced re.DOTALL and
the reviewer's revision changed it to re.MULTILINE, then the review strategy did
not merely fail to help — it actively broke a working fix. That distinction
matters a lot for the thesis, so it is worth reading the transcript rather than
inferring it from the final patch.
"""

import json
import sys
from pathlib import Path

ROOT = Path("D:/development/Skripsi2/AgantBech-SE")
out = Path(sys.argv[1])
art = ROOT / "results" / "EXP-20260927-007" / "artifacts"

_buf = []
def emit(s=""): _buf.append(s)


def show(instance: str, strategy: str) -> None:
    d = art / instance / strategy
    emit(f"########## {instance} / {strategy} ##########")
    if not d.exists():
        emit(f"  (no artifacts at {d})")
        emit()
        return
    for f in sorted(d.iterdir()):
        emit(f"  file: {f.name}  ({f.stat().st_size} bytes)")
    emit()

    mj = d / "messages.jsonl"
    if not mj.exists():
        emit("  (no messages.jsonl)")
        emit()
        return

    rows = []
    for line in mj.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    emit(f"  messages: {len(rows)}")
    for i, r in enumerate(rows):
        role = r.get("role") or r.get("agent") or "?"
        content = r.get("content") or ""
        if not isinstance(content, str):
            content = json.dumps(content)
        flag = ""
        if "re.DOTALL" in content:
            flag = "  <<< contains re.DOTALL"
        if "re.MULTILINE" in content:
            flag += "  <<< contains re.MULTILINE"
        emit(f"  [{i}] role={role} len={len(content)}{flag}")
    emit()

    # Print any message mentioning the regex flag, with surrounding context.
    for i, r in enumerate(rows):
        content = r.get("content") or ""
        if not isinstance(content, str):
            content = json.dumps(content)
        if "ordering_parts" in content or "re.DOTALL" in content or "re.MULTILINE" in content:
            role = r.get("role") or r.get("agent") or "?"
            emit(f"  ---- message [{i}] role={role} ----")
            for ln in content.splitlines():
                if "ordering_parts" in ln or "DOTALL" in ln or "MULTILINE" in ln:
                    emit(f"    {ln.strip()[:170]}")
            emit()


show("django__django-11001", "review")
show("django__django-11001", "direct")

out.write_text("\n".join(_buf) + "\n", encoding="utf-8")
print("ok")
