"""Is a strategy's pool SPLIT across its acts, or SHARED between them?

This exists because the fairness tool prints "review 148+10+10", which reads like a
hard three-way split -- and a reader can reasonably ask "why is review's executor
capped at 10 turns when direct gets 200?"

It is not a split. In per_task mode ``share()`` grants an act

    remaining - floor * (acts still to come)

so an act receives EVERYTHING left except the floors reserved for the acts after
it, and ``spend()`` returns whatever it did not use to the pool. The 148+10+10
figure is the WORST CASE (every act burning its full grant), not an allocation.

This script shows the difference with real numbers:

  A. worst case -- every act consumes its whole grant
  B. realistic   -- the turn counts the 15-run pilot actually measured

Usage:
    python tools/explain_pool_sharing.py
    python tools/explain_pool_sharing.py --total 200 --reserve 32 --floor 10
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.budget import ToolTurnBudget  # noqa: E402

#: What each review act actually USED, measured from the 15-run pilot
#: (EXP-20260930-215) by counting tool calls per act in tool_calls.jsonl.
#:
#: The planner always RUNS (all five runs have planner.md), but it does not always
#: call a tool -- on 10914 it answered from the issue text alone, so its entry is
#: absent from tool_calls.jsonl. The lists below therefore count CALLS per act and
#: are consumed in order, clamped to each act's grant; they are not a fixed-length
#: allocation. A revision round adds acts (11019 has five).
PILOT_REVIEW: dict[str, list[int]] = {
    "django__django-10914": [17, 10],
    "django__django-10924": [7, 22, 12],
    "django__django-11001": [10, 17],
    "django__django-11019": [12, 13, 11, 4, 3],
    "django__django-11039": [6, 11],
}


def walk(total: int, floor: int, reserve: int, used_per_act: list[int] | None,
         label: str) -> None:
    """Run review's base act sequence and report each act's grant.

    ``used_per_act=None`` means the worst case: every act spends everything it is
    granted, so nothing returns to the pool.
    """
    b = ToolTurnBudget(total=total, floor=floor, mode="per_task",
                       revision_reserve=reserve)
    pool_at_start = b.remaining

    print(f"\n  {label}")
    print(f"    base pool at start: {pool_at_start} "
          f"(total {total} - reserve {reserve})")

    acts = [("planner", 3), ("executor", 2), ("reviewer", 1)]
    for i, (role, acts_to_come) in enumerate(acts):
        grant = b.share(acts_to_come)
        if used_per_act is None:
            used = grant
        else:
            # A run may have fewer or more recorded acts than this 3-act base
            # sequence; the planner is skipped when the strategy does not use one,
            # and a revision adds acts. Consume in order and clamp.
            used = min(grant, used_per_act[i] if i < len(used_per_act) else grant)
        b.spend(used)
        freed = grant - used
        note = "" if used_per_act is None else f"   (freed {freed} back)"
        print(f"    {role:<9} granted {grant:>4}  used {used:>4}  "
              f"pool left {b.remaining:>4}{note}")

    print(f"    -> a revision could draw {b.remaining} from the base pool "
          f"plus {b.revision_remaining} reserve")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--total", type=int, default=200)
    ap.add_argument("--floor", type=int, default=10)
    ap.add_argument("--reserve", type=int, default=32)
    args = ap.parse_args()

    print("=" * 78)
    print(f"  POOL SHARING -- total={args.total} floor={args.floor} "
          f"reserve={args.reserve}")
    print("=" * 78)

    print()
    print("=" * 78)
    print("  A. WORST CASE -- every act burns its entire grant")
    print("=" * 78)
    walk(args.total, args.floor, args.reserve, None,
         "planner takes all it can, then executor, then reviewer")
    print(f"""
    This is what the fairness tool prints. It is a BOUND, not a plan: it answers
    "if the first act spends everything, do the later acts still get to work?"
    -- yes, each keeps its floor of {args.floor}.""")

    print()
    print("=" * 78)
    print("  B. REALISTIC -- the turn counts the pilot actually measured")
    print("=" * 78)
    for inst, used in PILOT_REVIEW.items():
        walk(args.total, args.floor, args.reserve, used,
             f"{inst}  (acts used {used})")

    print()
    print("=" * 78)
    print("  What this means")
    print("=" * 78)
    print(f"""
  The pool is SHARED, not split. An act is granted "everything left, minus
  {args.floor} for each act still to come", and whatever it does not use returns to
  the pool. Review's executor is therefore not capped at {args.floor} turns -- in
  the realistic runs it was granted most of the pool, because the planner used
  7-12 turns and handed the rest back.

  Why not open the FULL {args.total} to every act independently? Because then one
  strategy could consume {args.total} x 3 acts = {args.total * 3} turns while direct
  gets {args.total}. Review would have 3x the budget, which is exactly the confound
  the equal-total design exists to remove.

  The reference implementations never face this choice: mini-SWE-agent and SWE-agent
  run ONE agent per task, so "per task" and "per act" are the same thing. With three
  agents the pool must say how they SHARE it. That decision is ours -- and the floor
  is what keeps it fair: the later acts cannot be starved by an earlier one.
""")


if __name__ == "__main__":
    main()
