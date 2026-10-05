"""Import each module FIRST, in isolation, to catch import cycles.

The test suite can pass while a cycle exists, because it imports modules in a
favourable order. Importing a module directly (fresh interpreter, that module
first) is what actually proves the dependency graph is acyclic — a cycle that
"works by accident of import order" broke this project before.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path("D:/development/Skripsi2/AgantBech-SE")
out = Path(sys.argv[1])

MODULES = [
    "providers.tool_loop",
    "agents.base",
    "agents.tools",
    "providers.system_prompts",
    "strategies.review_strategy",
    "agents.registry",
    "providers.openrouter_provider",
    "providers.opencode_provider",
    "providers.commandcode_provider",
]

lines = []
fails = 0
for mod in MODULES:
    proc = subprocess.run(
        [sys.executable, "-c", f"import {mod}; print('OK')"],
        cwd=str(ROOT / "src"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    ok = proc.returncode == 0 and "OK" in proc.stdout
    if not ok:
        fails += 1
    lines.append(f"{'OK  ' if ok else 'FAIL'} {mod}")
    if not ok:
        err = (proc.stderr or "").strip().splitlines()
        for e in err[-4:]:
            lines.append(f"       {e[:150]}")

lines.append("")
lines.append(f"failures: {fails}")
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("ok")
