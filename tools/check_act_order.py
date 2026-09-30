"""Check the act order against what each strategy is supposed to do.

Expected sequences:

    direct    [direct]
    planning  [planner, executor]
    review    [planner, executor, reviewer]  then (executor, reviewer) per revision round

Anything else is an anomaly. Two independent records are checked, because they can
disagree: ``messages.jsonl`` (the blackboard, which includes acts that made no tool
calls) and ``trajectory.jsonl`` (which only exists for acts that ran the tool loop).

Usage:
    python tools/check_act_order.py --exp EXP-20260930-332
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXPECTED = {
    "direct": ["direct"],
    "planning": ["planner", "executor"],
}


def read_messages(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def result_order(messages: list[dict]) -> list[str]:
    """The sender of every RESULT message, in order -- the actual act sequence."""
    return [m.get("sender", "?") for m in messages if m.get("kind") == "result"]


def trajectory_acts(path: Path) -> list[tuple[int, str]]:
    acts: list[tuple[int, str]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = (e.get("act_index"), e.get("act_role"))
        if key not in acts:
            acts.append(key)
    return acts


def tool_sequence(path: Path) -> list[tuple[str, str]]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            c = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append((c.get("agent", "?"), c.get("tool", "?")))
    return out


def check(exp: Path, max_revision_rounds: int) -> int:
    problems = 0
    print("=" * 78)
    print(f"  ACT ORDER CHECK -- {exp.name}")
    print("=" * 78)

    for inst_dir in sorted((exp / "artifacts").iterdir()):
        if not inst_dir.is_dir():
            continue
        for strategy in ("direct", "planning", "review"):
            art = inst_dir / strategy
            if not art.is_dir():
                continue

            m = art / "messages.jsonl"
            if not m.exists():
                continue
            messages = read_messages(m)
            order = result_order(messages)

            # Build the allowed patterns.
            if strategy == "direct":
                allowed = [["direct"]]
            elif strategy == "planning":
                allowed = [["planner", "executor"]]
            else:
                allowed = [["planner", "executor", "reviewer"]]
                for rounds in range(1, max_revision_rounds + 1):
                    allowed.append(
                        ["planner", "executor", "reviewer"]
                        + ["executor", "reviewer"] * rounds
                    )

            ok = order in allowed
            flag = "OK  " if ok else "ODD "
            print(f"\n  {flag} {inst_dir.name:<24} {strategy:<9} {order}")
            if not ok:
                problems += 1
                print(f"       expected one of:")
                for pattern in allowed[:3]:
                    print(f"         {pattern}")
                if len(allowed) > 3:
                    print(f"         ... ({len(allowed) - 3} more revision patterns)")

                # Show what the blackboard actually contains, in order, so the
                # anomaly can be read rather than guessed at.
                print(f"       full message sequence:")
                for i, msg in enumerate(messages):
                    calls = len(msg.get("tool_calls") or [])
                    ops = ",".join(msg.get("bb_ops") or [])
                    preview = (msg.get("content") or "").replace("\n", " ")[:44]
                    print(f"         [{i:>2}] {msg.get('sender'):<12} "
                          f"-> {msg.get('receiver'):<12} {msg.get('kind'):<7} "
                          f"calls={calls:<3} ops={ops:<22} {preview!r}")

            # Cross-check the trajectory's own act numbering.
            tj = art / "trajectory.jsonl"
            if tj.exists():
                acts = trajectory_acts(tj)
                roles = [r for _, r in acts]
                if roles != [a for a in order if a != "direct" or strategy == "direct"]:
                    print(f"       trajectory roles: {roles}  "
                          f"(differs from the result order above)")

            # Intra-act tool order: an edit before any read is worth seeing.
            tc = art / "tool_calls.jsonl"
            if tc.exists():
                seq = tool_sequence(tc)
                first_edit = next((i for i, (_, t) in enumerate(seq)
                                   if t in ("edit_file", "write_file")), None)
                first_read = next((i for i, (_, t) in enumerate(seq)
                                   if t in ("read_file", "grep", "list_files")), None)
                if first_edit is not None and (first_read is None or first_edit < first_read):
                    print(f"       NOTE: an edit came before any read "
                          f"(edit at call {first_edit + 1}, "
                          f"first read at "
                          f"{'none' if first_read is None else first_read + 1})")

    print()
    print("=" * 78)
    if problems:
        print(f"  {problems} run(s) with an unexpected act order")
    else:
        print("  Every run's act order matches its strategy's design")
    print("=" * 78)
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exp", default=None)
    ap.add_argument("--max-revision-rounds", type=int, default=1)
    args = ap.parse_args()

    if args.exp:
        exp = ROOT / "results" / args.exp
    else:
        exp = sorted((ROOT / "results").glob("EXP-*"),
                     key=lambda p: p.stat().st_mtime)[-1]

    raise SystemExit(1 if check(exp, args.max_revision_rounds) else 0)


if __name__ == "__main__":
    main()
