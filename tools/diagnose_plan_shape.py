"""Why is the planner's `affected_files` field empty in half the plans?

The executor reads that field to know which files to touch, so an empty list means
the plan's file targeting is effectively lost -- the paths survive only in prose.

Two candidate causes, and they need different fixes:

* the model returned a DIFFERENT JSON shape (e.g. prose followed by JSON, so a
  strict parse fails and the field is invisible to a reader looking for JSON);
* the model returned the right shape with an empty list.

This inspects the raw text so the cause is measured rather than assumed.

Usage:
    python tools/diagnose_plan_shape.py --exp EXP-20260930-415
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def extract_json_block(text: str) -> tuple[dict | None, str]:
    """Try to find a JSON object in the text. Returns (payload, how)."""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped), "clean JSON"
        except json.JSONDecodeError:
            pass

    # Prose followed by a fenced or bare JSON block.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        try:
            return json.loads(fence.group(1)), "fenced JSON after prose"
        except json.JSONDecodeError:
            pass

    # Last balanced object in the text.
    start = text.rfind("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        return json.loads(candidate), "bare JSON after prose"
                    except json.JSONDecodeError:
                        break
        start = text.rfind("{", 0, start)
    return None, "NO JSON FOUND"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exp", default=None)
    args = ap.parse_args()

    exp = (ROOT / "results" / args.exp if args.exp
           else sorted((ROOT / "results").glob("EXP-*"), key=lambda p: p.stat().st_mtime)[-1])

    print("=" * 78)
    print(f"  PLANNER OUTPUT SHAPE -- {exp.name}")
    print("=" * 78)

    shapes: dict[str, int] = {}
    empty_field: list[str] = []
    total = 0

    for m in sorted(exp.glob("artifacts/*/*/messages.jsonl")):
        for line in m.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("sender") != "planner" or d.get("kind") != "result":
                continue
            total += 1
            text = d.get("content") or ""
            payload, how = extract_json_block(text)
            shapes[how] = shapes.get(how, 0) + 1

            label = f"{m.parent.parent.name}/{m.parent.name}"
            if payload is None:
                print(f"\n  {label}: {how}")
                print(f"    first 200 chars: {text[:200]!r}")
                continue

            files = payload.get("affected_files")
            n = len(files) if isinstance(files, list) else -1
            print(f"\n  {label}")
            print(f"    shape   : {how}")
            print(f"    keys    : {sorted(payload.keys())}")
            print(f"    affected_files: {n} entr{'y' if n == 1 else 'ies'}")
            if n == 0:
                empty_field.append(label)
            elif n > 0:
                for entry in files[:3]:
                    print(f"      - {str(entry)[:80]}")

    print()
    print("=" * 78)
    print("  SHAPES SEEN")
    print("=" * 78)
    for how, n in sorted(shapes.items(), key=lambda kv: -kv[1]):
        print(f"    {n:>3}  {how}")

    print()
    print("=" * 78)
    print(f"  affected_files EMPTY in {len(empty_field)} of {total} plans")
    if empty_field:
        for label in empty_field:
            print(f"    {label}")
        print("""
  An empty list means the executor gets a plan with no file targeting from that
  field. If the model put the paths in prose instead, the executor may still find
  them -- but the structured signal the pipeline relies on is absent.""")
    print("=" * 78)


if __name__ == "__main__":
    main()
