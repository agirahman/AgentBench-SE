"""Summarize a generation_result.csv: per-strategy timing, tokens, inference, statuses."""

import csv
import sys
from pathlib import Path

path = Path(sys.argv[1])
out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
rows = list(csv.DictReader(path.open(encoding="utf-8")))

# Buffer and write ourselves: PowerShell's `>` redirect emits UTF-16LE, which
# later reads as UTF-8 turn into NUL-interleaved garbage.
_buf: list[str] = []


def emit(line: str = "") -> None:
    _buf.append(line)


def flush() -> None:
    text = "\n".join(_buf) + "\n"
    if out_path is not None:
        out_path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


emit(f"rows: {len(rows)}")
emit(f"columns: {list(rows[0].keys())}")
emit()

# Aggregate by strategy
from collections import defaultdict

agg = defaultdict(lambda: {"n": 0, "time": 0.0, "tok": 0, "inf": 0, "statuses": []})
for r in rows:
    s = r.get("strategy", "?")
    a = agg[s]
    a["n"] += 1
    try:
        a["time"] += float(r.get("execution_time") or 0)
    except ValueError:
        pass
    try:
        a["tok"] += int(float(r.get("total_tokens") or 0))
    except ValueError:
        pass
    try:
        a["inf"] += int(float(r.get("inference_count") or 0))
    except ValueError:
        pass
    a["statuses"].append(r.get("patch_status", "?"))

emit(f"{'strategy':10} {'n':>3} {'total_s':>9} {'avg_s':>8} {'tokens':>10} {'infer':>6} statuses")
emit("-" * 78)
for s in ("direct", "planning", "review"):
    if s not in agg:
        continue
    a = agg[s]
    avg = a["time"] / a["n"] if a["n"] else 0
    emit(f"{s:10} {a['n']:>3} {a['time']:>9.1f} {avg:>8.1f} {a['tok']:>10} {a['inf']:>6} {a['statuses']}")

emit()
emit(f"apply_status column present: {'apply_status' in rows[0]}")
if "apply_status" in rows[0]:
    from collections import Counter

    c = Counter(r.get("apply_status", "?") for r in rows)
    emit(f"apply_status counts: {dict(c)}")
    emit()
    for r in rows:
        emit(f"  {r.get('instance_id','?'):26} {r.get('strategy','?'):9} "
             f"patch={r.get('patch_status','?'):10} apply={r.get('apply_status','?')}")

emit()
emit(f"errors: {sum(1 for r in rows if r.get('error'))}")

flush()
