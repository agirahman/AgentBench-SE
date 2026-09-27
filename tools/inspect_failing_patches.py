"""Show what the failing patches actually changed, next to the passing ones.

All failing patches applied cleanly, so the failures are semantic: the fix was
wrong or incomplete, not malformed. Comparing patch size and touched files
between a passing and a failing strategy on the SAME instance is the quickest
way to see whether review is over-editing.
"""

import re
import sys
from pathlib import Path

ROOT = Path("D:/development/Skripsi2/AgantBech-SE")
exp = ROOT / "results" / "EXP-20260927-007"
out = Path(sys.argv[1])

CASES = [
    ("django__django-10924", ["direct", "planning", "review"], {"direct": False, "planning": True, "review": False}),
    ("django__django-11001", ["direct", "planning", "review"], {"direct": True, "planning": True, "review": False}),
]

_buf = []
def emit(s=""): _buf.append(s)

for iid, strategies, resolved in CASES:
    emit(f"=== {iid} ===")
    for s in strategies:
        pf = exp / "patches" / f"{iid}_{s}.txt"
        if not pf.exists():
            emit(f"  {s}: NO PATCH")
            continue
        text = pf.read_text(encoding="utf-8")
        files = re.findall(r"^diff --git a/(\S+)", text, re.M)
        add = len(re.findall(r"^\+(?!\+\+)", text, re.M))
        rem = len(re.findall(r"^-(?!--)", text, re.M))
        verdict = "RESOLVED" if resolved[s] else "FAILED"
        emit(f"  {s:9} [{verdict:8}] {len(text):>6}B  +{add}/-{rem}  files={len(files)}")
        for f in files:
            emit(f"      {f}")
        # show hunk headers to reveal scope
        hunks = re.findall(r"^@@[^@]*@@.*$", text, re.M)
        if hunks:
            emit(f"      hunks: {len(hunks)}")
            for h in hunks[:6]:
                emit(f"        {h[:100]}")
    emit()

out.write_text("\n".join(_buf) + "\n", encoding="utf-8")
print("ok")
