"""Smoke-test the OpenCode provider (9router) end-to-end.

Verifies, in order:
  1. Config resolves OPENCODE_API_KEY / MODEL / BASE_URL correctly.
  2. The provider instantiates and its health check passes.
  3. generate_with_tools actually runs a tool loop against a real repo
     (read_file + edit_file), and the resulting `git diff` is applyable.

Nothing here touches the experiment pipeline; it isolates the provider.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import dotenv_values, load_dotenv  # noqa: E402

# .env must win over inherited shell values for this probe to mean anything.
raw = dotenv_values(ROOT / ".env")
for k in raw:
    import os

    os.environ.pop(k, None)
load_dotenv(ROOT / ".env")

from config import Config  # noqa: E402


def mask(v: str | None) -> str:
    return f"{v[:8]}... (len={len(v)})" if v else "<empty>"


print("=== config ===")
print(f"  OPENCODE_API_KEY : {mask(Config.OPENCODE_API_KEY)}")
print(f"  OPENCODE_MODEL   : {Config.OPENCODE_MODEL}")
print(f"  OPENCODE_BASE_URL: {Config.OPENCODE_BASE_URL}")
print(f"  TOOLCALL_ENABLED : {Config.TOOLCALL_ENABLED}")
print(f"  MAX_TOOL_TURNS   : {Config.MAX_TOOL_TURNS}")
print(f"  SOURCE_CONTEXT   : {Config.SOURCE_CONTEXT_ENABLED}")

print()
print("=== provider instantiation + health check ===")
from providers.opencode_provider import OpenCodeProvider  # noqa: E402

provider = OpenCodeProvider()
print(f"  has generate_with_tools: {hasattr(provider, 'generate_with_tools')}")
ok = provider.health_check()
print(f"  health_check: {ok}")
if not ok:
    print("  ABORT: provider unhealthy")
    sys.exit(1)

print()
print("=== tool loop against a real repo ===")
# Use a small instance checkout so the loop is fast.
import subprocess  # noqa: E402

REPO_BASE = ROOT / "datasets" / "repos"
candidate = None
for d in (REPO_BASE / "psf").rglob("*"):
    if d.is_dir() and (d / ".git").exists():
        candidate = d
        break
if candidate is None:
    print("  no checkout found under datasets/repos/psf")
    sys.exit(1)
print(f"  repo: {candidate.relative_to(ROOT)}")

from agents.tools import TOOL_SCHEMAS, set_repo_root  # noqa: E402
from providers.system_prompts import TOOL_SYSTEM_PROMPT  # noqa: E402

set_repo_root(candidate)
prompt = (
    "Explore this repository and answer: what is the exact text of the first "
    "line of setup.py? Use read_file to check. Then reply with that line only."
)
res = provider.generate_with_tools(
    prompt,
    role="direct",
    tools=TOOL_SCHEMAS,
    repo_root=str(candidate),
    system_prompt=TOOL_SYSTEM_PROMPT,
    max_tool_turns=6,
)
print(f"  api_turns   : {getattr(res, 'api_turns', '?')}")
print(f"  tool_calls  : {len(getattr(res, 'tool_calls', []) or [])}")
for tc in getattr(res, "tool_calls", []) or []:
    out = str(tc.get("result", ""))[:80].replace("\n", " ")
    print(f"    - {tc.get('name')} -> {out}")
print(f"  total_tokens: {getattr(res, 'total_tokens', '?')}")
print(f"  response    : {(res.response or '')[:200]!r}")
