"""Compact status monitor for a running sweep.

The per-experiment results/<EXP>/logs/experiment.log stays empty during a run —
loguru writes to stdout, which our launcher redirects. So this reads the sweep's
stdout log (passed as argv[1]) and also counts artifacts on disk, which is the
signal that actually advances.

Printed to a file by the caller because inline PowerShell quoting mangles ANSI
log output badly enough to hide the real signal.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path("D:/development/Skripsi2/AgantBech-SE")
results = ROOT / "results"

stdout_log = Path(sys.argv[1]) if len(sys.argv) > 1 else None
exp_name = sys.argv[2] if len(sys.argv) > 2 else None
out_path = Path(sys.argv[3]) if len(sys.argv) > 3 else None

# Buffer output and write it ourselves as UTF-8. If the caller redirects stdout
# with PowerShell's `>`, the file becomes UTF-16LE and later reads interleave NUL
# bytes, which is exactly the failure mode this script exists to avoid.
_buf: list[str] = []


def emit(line: str = "") -> None:
    _buf.append(line)


def flush() -> None:
    text = "\n".join(_buf) + "\n"
    if out_path is not None:
        out_path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)

if exp_name:
    exp = results / exp_name
else:
    cands = sorted([p for p in results.glob("EXP-*") if p.is_dir()], key=lambda p: p.stat().st_mtime)
    exp = cands[-1] if cands else None

if exp is None or not exp.exists():
    emit("no experiment dir found")
    flush(); sys.exit(1)

emit(f"experiment: {exp.name}")

if stdout_log and stdout_log.exists():
    # PowerShell's `>` redirect writes UTF-16LE (BOM FF FE), not UTF-8. Reading
    # such a log as UTF-8 interleaves NUL bytes and every regex silently fails,
    # which reads as "0 tool calls". Detect the BOM and decode accordingly.
    raw = stdout_log.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace")
        enc = "utf-16"
    elif raw[:3] == b"\xef\xbb\xbf":
        text = raw.decode("utf-8-sig", errors="replace")
        enc = "utf-8-sig"
    else:
        try:
            text = raw.decode("utf-8")
            enc = "utf-8"
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
            enc = "cp1252"
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    lines = text.splitlines()

    emit(f"  stdout log size: {stdout_log.stat().st_size} bytes  (encoding: {enc})")
    emit(f"  tracebacks     : {text.count('Traceback')}")
    emit(f"  429 errors     : {len(re.findall(r'429', text))}")
    emit(f"  max_turns hits : {len(re.findall(r'max_tool_turns|max_turns hit', text))}")
    emit(f"  tool calls     : {len(re.findall(r'\[toolcall\]', text))}")

    emit("  recent notable lines:")
    keep = []
    for ln in lines:
        if re.search(r"Completed |inferences|Total tokens|Patch |patch_status|Saved |Export|Error|WARNING", ln):
            keep.append(ln.strip()[:150])
    for ln in keep[-18:]:
        emit(f"    {ln}")
else:
    emit("  (stdout log not found)")

emit()
preds = exp / "predictions"
if preds.exists():
    emit("  predictions:")
    for f in sorted(preds.glob("*.jsonl")):
        if f.stem == "predictions":
            continue
        n, st = 0, []
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            n += 1
            st.append(d.get("patch_status", "?"))
        emit(f"    {f.stem:9} n={n} statuses={st}")

patches = exp / "patches"
if patches.exists():
    emit("  patches:")
    for f in sorted(patches.glob("*.txt")):
        emit(f"    {f.name:46} {f.stat().st_size:>7} bytes")

flush()
