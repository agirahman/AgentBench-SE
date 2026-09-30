"""Dump the ACT ORDER of a run, from both records, to see if they agree.

Two independent sources record the sequence, and they can disagree:

* ``messages.jsonl`` -- the blackboard conversation: every task handed to an agent
  and every result it returned, including acts that made ZERO tool calls.
* ``tool_calls.jsonl`` -- one line per executed call, so an act with no calls is
  INVISIBLE here.

Reading only tool_calls.jsonl therefore shows a sequence with holes, and the holes
are exactly the read-only acts (a planner that answers from the issue text). That
difference is not a bug, but it makes the act order look wrong.

Usage:
    python tools/show_act_order.py                      # newest experiment
    python tools/show_act_order.py --exp EXP-20260930-332
    python tools/show_act_order.py --exp EXP-... --strategy review
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def act_sequence_from_messages(path: Path) -> list[tuple[str, str, int, str]]:
    """(sender, kind, n_tool_calls, preview) for each message, in order."""
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        calls = d.get("tool_calls") or []
        content = (d.get("content") or "").replace("\n", " ")
        out.append((d.get("sender", "?"), d.get("kind", "?"), len(calls), content[:56]))
    return out


def act_sequence_from_tools(path: Path) -> list[tuple[str, str]]:
    """(agent, tool) per executed call, in order."""
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append((d.get("agent", "?"), d.get("tool", "?")))
    return out


def collapse_tools(seq: list[tuple[str, str]]) -> list[tuple[str, int]]:
    """Consecutive (agent, tool) -> (agent, call count) groups."""
    groups: list[tuple[str, int]] = []
    for agent, _tool in seq:
        if not groups or groups[-1][0] != agent:
            groups.append((agent, 1))
        else:
            groups[-1] = (agent, groups[-1][1] + 1)
    return groups


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exp", default=None)
    ap.add_argument("--strategy", default=None)
    args = ap.parse_args()

    if args.exp:
        exp = ROOT / "results" / args.exp
    else:
        candidates = sorted((ROOT / "results").glob("EXP-*"),
                            key=lambda p: p.stat().st_mtime)
        exp = candidates[-1]

    print("=" * 78)
    print(f"  ACT ORDER -- {exp.name}")
    print("=" * 78)

    strategies = [args.strategy] if args.strategy else ["direct", "planning", "review"]
    for strategy in strategies:
        d = exp / "artifacts"
        if not d.is_dir():
            continue
        for inst_dir in sorted(d.iterdir()):
            art = inst_dir / strategy
            if not art.is_dir():
                continue

            print(f"\n  {inst_dir.name}  /  {strategy}")
            print("  " + "-" * 74)

            m = art / "messages.jsonl"
            if m.exists():
                print("    messages.jsonl (ALL acts, incl. zero-call ones):")
                for i, (sender, kind, n, preview) in enumerate(
                    act_sequence_from_messages(m)
                ):
                    marker = "  <-- RESULT" if kind == "result" else ""
                    print(f"      [{i:>2}] {sender:<12} {kind:<7} calls={n:<3}"
                          f"{preview!r}{marker}")

            t = art / "tool_calls.jsonl"
            if t.exists():
                seq = act_sequence_from_tools(t)
                groups = collapse_tools(seq)
                print(f"    tool_calls.jsonl (only acts that CALLED something):")
                for agent, n in groups:
                    print(f"      {agent:<12} {n} call(s)")
                missing = {a for a, _ in groups}
                print(f"    NOTE: acts absent from tool_calls.jsonl: "
                      f"{sorted({'direct','planner','executor','reviewer'} - missing)}")

            tj = art / "trajectory.jsonl"
            if tj.exists():
                acts: list[tuple[int, str]] = []
                for line in tj.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = (e.get("act_index"), e.get("act_role"))
                    if key not in acts:
                        acts.append(key)
                print("    trajectory.jsonl act order:")
                for idx, role in acts:
                    print(f"      act_index={idx}  role={role}")


if __name__ == "__main__":
    main()
