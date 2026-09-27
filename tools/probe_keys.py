"""Probe which 9router key can see and call oc/space-bunny-free.

The .env line `OPENCODE_API_KEY=$NINEROUTER_API_KEY` is a literal string to
python-dotenv (it expands ${VAR}, not $VAR), so the key must come from the
environment. This script tries the candidate keys and reports, for each:
  * how many models it sees,
  * whether oc/space-bunny-free is among them,
  * whether that model answers AND supports tool calling.

Keys are never printed; only a masked fingerprint so they can be told apart.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import dotenv_values  # noqa: E402

BASE = "http://localhost:20128/v1"
MODEL = "oc/space-bunny-free"
raw = dotenv_values(ROOT / ".env")


def fingerprint(v: str) -> str:
    if not v:
        return "<empty>"
    return f"len={len(v)} sha={hash(v) & 0xFFFF:04x} head={v[:6]}"


candidates: dict[str, str] = {}

# From the environment (this is where NINEROUTER_API_KEY actually lives).
for name in ("NINEROUTER_API_KEY", "COMMANDCODE_API_KEY", "OPENCODE_API_KEY"):
    val = os.environ.get(name)
    if val:
        candidates[f"env:{name}"] = val

# From .env, but only if it looks like a real key (not an unexpanded $VAR).
for name in ("NINEROUTER_API_KEY", "COMMANDCODE_API_KEY", "OPENCODE_API_KEY"):
    val = raw.get(name)
    if val and not val.startswith("$"):
        candidates[f"file:{name}"] = val

print("=== candidate keys ===")
for label, val in candidates.items():
    print(f"  {label:28s} {fingerprint(val)}")

from openai import OpenAI  # noqa: E402

TOOLS = [{
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


def probe(label: str, key: str) -> None:
    print()
    print(f"=== {label} ===")
    client = OpenAI(api_key=key, base_url=BASE)

    # 1) model catalogue visible to this key
    try:
        ids = [m.id for m in client.models.list().data]
        oc = [m for m in ids if m.startswith("oc/")]
        print(f"  models visible : {len(ids)}   oc/* : {len(oc)}")
        if oc:
            print(f"    {oc[:10]}")
        print(f"  target present : {MODEL in ids}")
    except Exception as e:  # noqa: BLE001
        print(f"  models.list FAILED: {type(e).__name__}: {str(e)[:120]}")
        return

    # 2) plain generation
    try:
        r = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "Reply with only: PONG"}],
            max_tokens=32,
            timeout=90,
        )
        txt = (r.choices[0].message.content or "").strip()
        print(f"  generate       : OK finish={r.choices[0].finish_reason} content={txt[:60]!r}")
    except Exception as e:  # noqa: BLE001
        print(f"  generate FAILED: {type(e).__name__}: {str(e)[:160]}")
        return

    # 3) tool calling
    try:
        r = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "What is the weather in Jakarta? Call the tool."}],
            tools=TOOLS,
            max_tokens=200,
            timeout=120,
        )
        msg = r.choices[0].message
        tcs = getattr(msg, "tool_calls", None) or []
        print(f"  tool_calls     : {len(tcs)}  finish={r.choices[0].finish_reason}")
        for tc in tcs:
            print(f"    - {tc.function.name}({tc.function.arguments})")
        if not tcs:
            print(f"    content: {(msg.content or '')[:160]!r}")
    except Exception as e:  # noqa: BLE001
        print(f"  tools FAILED   : {type(e).__name__}: {str(e)[:160]}")


for label, val in candidates.items():
    probe(label, val)
