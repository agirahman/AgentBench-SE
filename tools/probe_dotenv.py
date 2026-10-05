"""Test how python-dotenv resolves variable references in .env.

The file currently has `OPENCODE_API_KEY=$NINEROUTER_API_KEY`, which python-dotenv
stores as a LITERAL 19-char string (it interpolates ${VAR}, not $VAR). This script
checks whether `${VAR}` form works, and from which source it resolves
(the .env file itself, or os.environ).
"""

import os
import tempfile
from pathlib import Path

from dotenv import dotenv_values

os.environ["OUTER_VAR"] = "sk-from-environment"

cases = {
    "literal $VAR   ": "OPENCODE_API_KEY=$OUTER_VAR\n",
    "braced ${VAR}  ": "OPENCODE_API_KEY=${OUTER_VAR}\n",
    "braced default ": "OPENCODE_API_KEY=${MISSING_VAR:-sk-fallback}\n",
}

for label, body in cases.items():
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8") as f:
        f.write(body)
        path = Path(f.name)
    vals = dotenv_values(path)
    got = vals.get("OPENCODE_API_KEY")
    verdict = "EXPANDED" if got == "sk-from-environment" else f"literal/other ({got!r})"
    print(f"  {label} -> {verdict}")
    path.unlink()

print()
print("=== does a value defined IN the .env file expand? ===")
with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8") as f:
    f.write("MY_KEY=sk-inner\nALIAS=${MY_KEY}\n")
    path = Path(f.name)
vals = dotenv_values(path)
print(f"  ALIAS=${{MY_KEY}} -> {vals.get('ALIAS')!r}")
path.unlink()

print()
print("=== what does the real .env resolve to now? ===")
root = Path(__file__).resolve().parent.parent
real = dotenv_values(root / ".env")
v = real.get("OPENCODE_API_KEY")
print(f"  OPENCODE_API_KEY = {v!r}  (len={len(v) if v else 0})")
print(f"  starts with '$'? {str(v).startswith('$')}  -> would 401 if used")
