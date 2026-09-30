"""Read the FULL text of every command the tool guard refused.

The log truncates the command to 160 chars, so a preview can look innocent while
the rest of the line writes a file. This reads the recorded call instead, so the
verdict is about the whole command rather than its first 160 characters.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents import tools as T

exp = sorted((ROOT / "results").glob("EXP-*"), key=lambda p: p.stat().st_mtime)[-1]
print(f"experiment: {exp.name}")
print()

found = 0
for tc in sorted(exp.glob("artifacts/*/*/tool_calls.jsonl")):
    for line in tc.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        result = c.get("result_preview") or ""
        if "read-only" not in result or "may not modify" not in result:
            continue
        found += 1
        command = (c.get("arguments") or {}).get("command", "")
        print("=" * 78)
        print(f"  {tc.parent.parent.name} / {tc.parent.name} / {c['agent']}")
        print("=" * 78)
        print(f"  length: {len(command)} chars")
        print()
        print("  FULL COMMAND:")
        for i, ln in enumerate(command.splitlines(), 1):
            print(f"    {i:>2}| {ln}")
        print()
        print(f"  classifier says: {T._command_looks_like_a_write(command)!r}")
        print()

if not found:
    print("  no refused commands in this experiment")

print("=" * 78)
print(f"  {found} refused command(s)")
print("=" * 78)
