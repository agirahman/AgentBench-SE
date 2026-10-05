"""Verify the headline finding: review now RESOLVES 11001.

In EXP-20260928-003 review failed 11001 because the revision act was granted
1 turn (enough to read two files, not to edit one). If the fix worked, this run
must show: review verdict NEEDS_REVISION -> an executor act -> a second review,
and the revision act must contain at least one EDIT.
"""
import json
import pathlib

art = pathlib.Path("results/EXP-20260929-003/artifacts/django__django-11001/review")
print("artifacts:", art.exists())
if not art.exists():
    raise SystemExit("no artifacts")

# 1. Message sequence: who talked to whom, in order.
msgs = art / "messages.jsonl"
print("\n" + "=" * 78)
print("MESSAGE SEQUENCE")
print("=" * 78)
if msgs.exists():
    for i, line in enumerate(msgs.read_text(encoding="utf-8", errors="ignore").splitlines()):
        if not line.strip():
            continue
        m = json.loads(line)
        kind = m.get("kind", "?")
        sender = m.get("sender", "?")
        receiver = m.get("receiver", "?")
        ops = m.get("bb_ops") or []
        print(f"  {i:>2}. {kind:<8} {sender:>12} -> {receiver:<12} {ops}")

# 2. Tool calls per agent: did the revision act EDIT?
tc = art / "tool_calls.jsonl"
print("\n" + "=" * 78)
print("TOOL CALLS BY AGENT")
print("=" * 78)
if tc.exists():
    from collections import Counter, defaultdict
    per_agent = defaultdict(Counter)
    order = []
    for line in tc.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        agent = c.get("agent", "?")
        tool = c.get("tool", "?")
        per_agent[agent][tool] += 1
        order.append((agent, tool))
    for agent, counts in per_agent.items():
        total = sum(counts.values())
        print(f"  {agent:<12} {total:>3} calls  {dict(counts)}")

    print("\n  EDIT positions in the full call order:")
    for i, (agent, tool) in enumerate(order):
        if tool in ("edit_file", "write_file", "str_replace"):
            print(f"    #{i:>2} {agent:<12} {tool}")

    print("\n  First/last 6 calls:")
    for i, (agent, tool) in enumerate(order[:6]):
        print(f"    #{i:>2} {agent:<12} {tool}")
    print("    ...")
    for i, (agent, tool) in enumerate(order[-6:], start=len(order) - 6):
        print(f"    #{i:>2} {agent:<12} {tool}")

# 3. Verdicts
print("\n" + "=" * 78)
print("REVIEWER VERDICTS")
print("=" * 78)
rev = art / "reviewer.md"
if rev.exists():
    text = rev.read_text(encoding="utf-8", errors="ignore")
    import re
    for m in re.finditer(r'"verdict"\s*:\s*"([A-Z_]+)"', text):
        print(f"  verdict: {m.group(1)}")
    print(f"  ({len(text)} chars)")
    idx = text.find('"verdict"')
    if idx >= 0:
        print("  excerpt:", text[max(0, idx - 200):idx + 300].replace("\n", " ")[:500])

# 4. Executor's final message (should mention what it fixed)
ex = art / "executor.md"
if ex.exists():
    t = ex.read_text(encoding="utf-8", errors="ignore")
    print("\n  executor.md (last 600 chars):")
    print("   ", t[-600:].replace("\n", " "))
