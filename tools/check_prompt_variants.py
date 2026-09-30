"""Which prompt does each role ACTUALLY get, and does a tool variant exist?

``BaseAgent._tool_template()`` (src/agents/base.py:50-68) loads
``<prompt_file>_tools.md`` for tool-calling mode, and falls back to the BASE
template when that file is absent.

That fallback is the bug: a role with no ``_tools`` variant silently receives its
NON-TOOL prompt, which for planner says "Output ONLY valid JSON" -- an instruction
that suppresses exploration. The role then makes zero tool calls while the harness
believes it is in tool mode.

This prints, per role, the template actually selected in tool mode and whether it
came from the tool variant or the fallback.

Usage:
    python tools/check_prompt_variants.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "src" / "prompts"
sys.path.insert(0, str(ROOT / "src"))

#: The role -> prompt_file mapping, read from the agent classes so this check
#: cannot drift from the code it describes.
def role_prompt_files() -> dict[str, str]:
    from agents import direct_agent, executor_agent, planner_agent, reviewer_agent

    out = {}
    for mod, cls in (
        (direct_agent, "DirectAgent"),
        (planner_agent, "PlannerAgent"),
        (executor_agent, "ExecutorAgent"),
        (reviewer_agent, "ReviewerAgent"),
    ):
        klass = getattr(mod, cls, None)
        if klass is None:
            continue
        out[klass.name] = klass.prompt_file
    return out


def has_source_code_reference(text: str) -> list[str]:
    """Lines referencing the dead passive-context feature."""
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        if re.search(r"SOURCE CODE|base commit", line, re.I):
            hits.append(f"line {i}: {line.strip()[:90]}")
    return hits


def main() -> None:
    print("=" * 78)
    print("  PROMPT VARIANT RESOLUTION (tool-calling mode)")
    print("=" * 78)

    mapping = role_prompt_files()
    problems = []

    print(f"\n  {'role':<10} {'prompt_file':<24} {'tool variant':<26} selected")
    print(f"  {'-' * 76}")

    for role in sorted(mapping):
        base = mapping[role]
        stem = base[:-3] if base.endswith(".md") else base
        tool_name = f"{stem}_tools.md"
        tool_path = PROMPTS / tool_name
        exists = tool_path.exists()
        selected = tool_name if exists else f"{base}  (FALLBACK)"
        mark = "" if exists else "   <-- no tool variant"
        print(f"  {role:<10} {base:<24} "
              f"{'yes' if exists else 'MISSING':<26} {selected}{mark}")
        if not exists:
            problems.append((role, base, tool_name))

    print()
    print("=" * 78)
    print("  DEAD 'SOURCE CODE' REFERENCES")
    print("=" * 78)
    print("""
  These reference a passive-context feature that no longer injects anything. A
  model reading them is told to use file paths "provided below" that never appear,
  so the instruction is not merely useless -- it invites invented paths.
""")
    for path in sorted(PROMPTS.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = has_source_code_reference(text)
        if hits:
            print(f"\n  {path.name}")
            for h in hits:
                print(f"    {h}")

    print()
    print("=" * 78)
    print("  VERDICT")
    print("=" * 78)
    if problems:
        print(f"\n  {len(problems)} role(s) silently fall back to a NON-TOOL prompt:")
        for role, base, tool_name in problems:
            print(f"    {role}: expected {tool_name}, got {base}")
        print("""
  A role in this state runs with instructions written for the text-diff pipeline:
  "Output ONLY valid JSON". It does not explore, because nothing told it to -- and
  nothing in the results marks the difference, so it looks like the agent chose not
  to read code.""")
    else:
        print("\n  Every role has a tool variant; no silent fallback.")


if __name__ == "__main__":
    main()
