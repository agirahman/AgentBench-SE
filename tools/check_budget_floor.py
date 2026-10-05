"""What floor keeps the later acts safe, now that the pool is 200?

The pool is SHARED between a strategy's acts, not split (see
tools/explain_pool_sharing.py). Sharing means the FIRST act can consume it, and the
floor is the only guarantee the later acts have.

That mattered less at pool 40, where a greedy planner could take at most 40. At 200
the worst case is planner 148 / executor 10 / reviewer 10 -- and the pilot, run at
pool 40, measured the executor USING 13-22 turns. A floor of 10 is therefore below
what the executor needed, so a planner that spends its grant could starve it.

The floor costs nothing when acts behave: it is reserved, then returned to the pool
the moment an act does not use it. Raising it changes only the greedy case.

Usage:
    python tools/check_budget_floor.py
    python tools/check_budget_floor.py --total 200 --reserve 32
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.budget import ToolTurnBudget  # noqa: E402

#: Worst turn count each act used in the 15-run pilot (EXP-20260930-215), by
#: counting tool calls per act. The revision act is excluded: it was granted 4
#: turns and could not finish, which is the defect the reserve increase fixes.
PILOT_WORST = {"planner": 12, "executor": 22, "reviewer": 12}


def worst_case(total: int, reserve: int, floor: int) -> list[int]:
    """Grants when every act consumes everything it is given."""
    b = ToolTurnBudget(total=total, floor=floor, mode="per_task",
                       revision_reserve=reserve)
    grants = []
    for acts_to_come in (3, 2, 1):
        grant = b.share(acts_to_come)
        b.spend(grant)
        grants.append(grant)
    return grants


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--total", type=int, default=200)
    ap.add_argument("--reserve", type=int, default=32)
    args = ap.parse_args()

    print("=" * 78)
    print(f"  WORST CASE -- total={args.total}, reserve={args.reserve}")
    print("  (every act consumes its whole grant; nothing returns to the pool)")
    print("=" * 78)
    print()
    print(f"  {'floor':>6} {'planner':>9} {'executor':>10} {'reviewer':>10}  verdict")
    print(f"  {'-' * 70}")
    for floor in (10, 20, 30, 40, 50, 60, 70):
        p, e, r = worst_case(args.total, args.reserve, floor)
        if e < PILOT_WORST["executor"]:
            verdict = f"executor starved (needs ~{PILOT_WORST['executor']})"
        elif e < 2 * PILOT_WORST["executor"]:
            verdict = "executor has room, not slack"
        else:
            verdict = "comfortable"
        print(f"  {floor:>6} {p:>9} {e:>10} {r:>10}  {verdict}")

    print()
    print("=" * 78)
    print("  What the pilot measured (at pool 40, so grants were smaller)")
    print("=" * 78)
    for role, turns in PILOT_WORST.items():
        print(f"    {role:<9} worst {turns:>3} turns")

    print()
    print("=" * 78)
    print(f"  Realistic case at floor 40, using the pilot's worst turn counts")
    print("=" * 78)
    b = ToolTurnBudget(total=args.total, floor=40, mode="per_task",
                       revision_reserve=args.reserve)
    print(f"\n  base pool at start: {b.remaining}")
    for role, acts_to_come in (("planner", 3), ("executor", 2), ("reviewer", 1)):
        grant = b.share(acts_to_come)
        used = min(grant, PILOT_WORST[role])
        b.spend(used)
        print(f"    {role:<9} granted {grant:>4}  used {used:>4}  "
              f"pool left {b.remaining:>4}")
    print(f"    -> a revision could draw {b.remaining} base + "
          f"{b.revision_remaining} reserve")

    print()
    print("=" * 78)
    print("  Recommendation")
    print("=" * 78)
    print(f"""
  floor=40 at total={args.total} keeps every act above what the pilot measured, even
  in the worst case, and costs nothing when acts behave: the reservation returns to
  the pool as soon as an act does not spend it.

  The trade to state in the thesis: with floor=40 the WORST case is
  planner {worst_case(args.total, args.reserve, 40)[0]} / executor 40 / reviewer 40,
  i.e. a greedy first act takes {worst_case(args.total, args.reserve, 40)[0]} turns.
  That is not a defect -- it is the pool working as designed, and the floor is what
  stops it becoming starvation. Report the ACTUAL grants from the run rather than
  the bound.
""")


if __name__ == "__main__":
    main()
