"""Probe: does the opencode provider config actually work against 9router?

Checks three things before any code change:
  1. How OPENCODE_API_KEY resolves (it is written as $NINEROUTER_API_KEY).
  2. Whether the configured model (oc/space-bunny-free) exists in 9router.
  3. Whether that model supports tool calling — the whole point of the run.

Prints nothing secret: keys are masked to a prefix and length.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import dotenv_values, load_dotenv  # noqa: E402

raw = dotenv_values(ROOT / ".env")


def mask(v: str | None) -> str:
    if not v:
        return "<empty/None>"
    return f"{v[:10]}... (len={len(v)})"


print("=== raw .env values (as written) ===")
for k in ("OPENCODE_API_KEY", "NINEROUTER_API_KEY", "COMMANDCODE_API_KEY",
          "OPENCODE_MODEL", "COMMANDCODE_BASE_URL"):
    print(f"  {k} = {mask(raw.get(k)) if 'KEY' in k else raw.get(k)}")

print()
print("=== does python-dotenv expand $NINEROUTER_API_KEY? ===")
print(f"  NINEROUTER_API_KEY in .env file : {raw.get('NINEROUTER_API_KEY') is not None}")
print(f"  NINEROUTER_API_KEY in os.environ: {os.environ.get('NINEROUTER_API_KEY') is not None}")

load_dotenv(ROOT / ".env", override=True)
opencode_key = os.environ.get("OPENCODE_API_KEY", "")
print(f"  resolved OPENCODE_API_KEY       : {mask(opencode_key)}")
print(f"  is it still the literal '$...'? : {opencode_key.startswith('$')}")

print()
print("=== 9router: is the model present? ===")
try:
    from openai import OpenAI

    key = os.environ.get("COMMANDCODE_API_KEY") or opencode_key
    client = OpenAI(api_key=key, base_url="http://localhost:20128/v1")
    models = [m.id for m in client.models.list().data]
    print(f"  total models: {len(models)}")
    for want in ("oc/space-bunny-free", "cmd/deepseek/deepseek-v4-flash"):
        hits = [m for m in models if want in m]
        print(f"  {want:34s} -> {'FOUND: ' + str(hits[:3]) if hits else 'NOT FOUND'}")
    oc = [m for m in models if m.startswith("oc/")]
    print(f"  oc/* models ({len(oc)}): {oc[:12]}")
except Exception as e:  # noqa: BLE001
    print(f"  ERROR: {type(e).__name__}: {e}")

print()
print("=== tool calling support (oc/space-bunny-free) ===")
try:
    from openai import OpenAI

    key = os.environ.get("COMMANDCODE_API_KEY") or opencode_key
    client = OpenAI(api_key=key, base_url="http://localhost:20128/v1")
    tools = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }]
    r = client.chat.completions.create(
        model="oc/space-bunny-free",
        messages=[{"role": "user", "content": "What is the weather in Jakarta? Use the tool."}],
        tools=tools,
        max_tokens=200,
        timeout=90,
    )
    msg = r.choices[0].message
    tcs = getattr(msg, "tool_calls", None) or []
    print(f"  finish_reason : {r.choices[0].finish_reason}")
    print(f"  tool_calls    : {len(tcs)}")
    for tc in tcs:
        print(f"    - {tc.function.name}({tc.function.arguments})")
    print(f"  content       : {(msg.content or '')[:160]!r}")
except Exception as e:  # noqa: BLE001
    print(f"  ERROR: {type(e).__name__}: {e}")
