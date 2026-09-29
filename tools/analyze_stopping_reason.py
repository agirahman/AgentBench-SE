"""Does an act stop because it is DONE, or because it ran out of turns?

Distinguishes the two by looking at how many turns each act used against what it
was granted. An act that stops early had its own stopping criterion; an act that
uses its whole grant was cut off.

This matters for choosing a budget: if most acts stop early, the budget is not
what shapes their result and the exact number is not critical. If acts routinely
consume the full grant, the budget IS the stopping rule, and the number decides
the outcome -- which is the situation the thesis must not silently rely on.

Usage: python tools/analyze_stopping_reason.py <EXP> [--by-strategy]
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

CAP_RE = re.compile(r"hit max_tool_turns=(\d+) for role=(\w+)")
INSTANCE_RE = re.compile(r"Running (\w+) on (\S+)")


def main() -> None:
    exp = sys.argv[1] if len(sys.argv) > 1 else "EXP-20260928-003"
    log = ROOT / f"results/{exp}/logs/experiment.log"
    if not log.exists():
        print(f"missing {log}")
        return

    ctx: list[str] = []
    hits: dict[tuple[str, str, str], int] = {}
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = INSTANCE_RE.search(line)
        if m:
            ctx = [m.group(1), m.group(2)]
            continue
        c = CAP_RE.search(line)
        if c and len(ctx) == 2:
            key = (ctx[0], ctx[1], c.group(2))
            hits[key] = int(c.group(1))

    # tool_calls.jsonl records the calls per agent; an act that ended by itself
    # still has its calls recorded. Compare call counts to the grant.
    print(f"{exp}: cap-hit acts and their grants\n")
    by_role: dict[str, list[int]] = defaultdict(list)
    for (strat, inst, role), granted in sorted(hits.items()):
        print(f"  {strat:9s} {inst:24s} {role:9s} granted={granted}")
        by_role[role].append(granted)

    print()
    print("Acts that hit their cap, grouped by role:")
    for role, grants in sorted(by_role.items()):
        print(f"  {role:9s} {len(grants)} act(s), grants={grants}")

    print("\n--- what this means ---")
    print(
        "  Every act listed above was CUT OFF: its result measures the granted\n"
        "  number, not the agent's own judgement of when it was finished.\n"
        "  Acts not listed here ended by returning no tool call, i.e. the agent\n"
        "  decided it was done -- those results are independent of the exact cap."
    )


if __name__ == "__main__":
    main()
