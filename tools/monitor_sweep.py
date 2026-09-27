"""Compact status monitor for a running validation sweep.

Reads the experiment log and prints a short summary: config lines, per-step
progress, tool-call counts, and any error markers. ANSI codes are stripped so the
output stays readable in a captured file.
"""

import re
import sys
from collections import Counter
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def main() -> int:
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"log not found: {path}")
        return 1
    raw = path.read_text(encoding="utf-8", errors="replace")
    text = ANSI.sub("", raw)
    lines = text.splitlines()

    print(f"log: {path.name}  ({len(raw) / 1024:.0f} KB, {len(lines)} lines)")
    print()

    # Config lines printed by the wrapper / runner.
    for ln in lines[:80]:
        if any(k in ln for k in ("[env] ", "[run] ", "Experiment ID", "Provider",
                                 "Model:", "model=", "toolcall", "Strategi")):
            print(f"  cfg | {ln.strip()[:150]}")

    print()
    tools = Counter(re.findall(r"\[toolcall\] role=(\S+) tool=(\S+)", text))
    if tools:
        print("  tool calls:")
        for (role, tool), n in tools.most_common(12):
            print(f"    {role:9s} {tool:12s} {n:4d}")
    print()

    steps = re.findall(r"✅\s+([\d.]+)s \| (\d+) tokens.*?(\w+)\s*\|", text)
    ok = len(re.findall(r"✅", text))
    print(f"  completed steps: {ok}")
    for ln in lines:
        if "→ Patch saved" in ln or "✂" in ln or "max_turns" in ln:
            print(f"  note | {ln.strip()[:150]}")

    print()
    bad = [ln for ln in lines if any(k in ln for k in
           ("Traceback", "ERROR", "agent_prompt_stalled", "429", "rate limit"))]
    print(f"  error lines: {len(bad)}")
    for ln in bad[-6:]:
        print(f"    ! {ln.strip()[:150]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
