"""Does the revision reserve make the three strategies incomparable?

The reserve fix gives review's revision act real room. But the reserve is an
EXTRA allowance on top of the base pool, so review's total becomes
``total + revision_reserve`` while direct and planning stay at ``total``.

RQ1 asks which orchestration strategy is more effective, and RQ2 asks which is
more efficient. If review simply gets more turns, a better review result cannot be
attributed to the strategy -- it could be the larger budget. That is the confound
the whole per-task design was built to remove (see the module docstring in
budget.py: per-act caps made the total an accident of how many agents a strategy
has).

This tool states the numbers plainly so the trade-off is decided deliberately
rather than discovered in the results.

Usage:
    python tools/check_budget_fairness.py
    python tools/check_budget_fairness.py --total 40 --reserve 8 --floor 10
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agents.budget import ToolTurnBudget  # noqa: E402


def simulate(total: int, floor: int, reserve: int, mode: str = "per_task") -> dict:
    """Run each strategy's act sequence and report what it drew.

    Every act takes its whole grant (the worst case for fairness: nobody leaves
    anything behind), which is what actually happened in the recorded runs -- the
    truncation warnings show acts using their full allowance.

    ``with_revisions`` mirrors the callers: only review has revision acts, so only
    review carves the reserve out of its pool. Requesting it for direct or planning
    would cost them turns for acts they never run.
    """
    out = {}

    # direct: one act draws the whole pool.
    b = ToolTurnBudget(total=total, floor=floor, mode=mode, revision_reserve=0)
    direct_grant = b.share(1)
    out["direct"] = {"acts": [direct_grant], "total": direct_grant}

    # planning: planner then executor.
    b = ToolTurnBudget(total=total, floor=floor, mode=mode, revision_reserve=0)
    acts = []
    for acts_to_come in (2, 1):
        grant = b.share(acts_to_come)
        b.spend(grant)
        acts.append(grant)
    out["planning"] = {"acts": acts, "total": sum(acts)}

    # review: planner, executor, reviewer, then one revision round if the
    # reviewer rejects (which is the case this reserve exists for).
    b = ToolTurnBudget(total=total, floor=floor, mode=mode, revision_reserve=reserve)
    acts = []
    for acts_to_come in (3, 2, 1):
        grant = b.share(acts_to_come)
        b.spend(grant)
        acts.append(grant)
    base_total = sum(acts)
    revision_grants = []
    for acts_remaining in (2, 1):  # revision act, then the re-review
        grant = b.share_revision(acts_remaining)
        b.spend_revision(grant)
        revision_grants.append(grant)
    out["review"] = {
        "acts": acts,
        "revision_acts": revision_grants,
        "base_total": base_total,
        "total": base_total + sum(revision_grants),
    }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--total", type=int, default=40)
    parser.add_argument("--floor", type=int, default=10)
    parser.add_argument("--reserve", type=int, default=8,
                        help="REVISION_TOOL_TURNS (0 = the old starved behaviour)")
    parser.add_argument("--mode", default="per_task")
    args = parser.parse_args()

    print("=" * 78)
    print(f"  BUDGET FAIRNESS -- total={args.total} floor={args.floor} "
          f"reserve={args.reserve} mode={args.mode}")
    print("=" * 78)

    sim = simulate(args.total, args.floor, args.reserve, args.mode)

    print()
    print(f"  {'strategy':<10} {'base acts':<22} {'base':>6} {'revision':>9} {'TOTAL':>7}")
    print(f"  {'-' * 60}")
    for name in ("direct", "planning", "review"):
        s = sim[name]
        acts = "+".join(str(a) for a in s["acts"])
        rev = "+".join(str(a) for a in s.get("revision_acts", [])) or "-"
        rev_total = s["total"] - s["base_total"] if "base_total" in s else 0
        print(f"  {name:<10} {acts:<22} {s.get('base_total', s['total']):>6} "
              f"{rev_total:>9} {s['total']:>7}")

    base_totals = {n: sim[n].get("base_total", sim[n]["total"]) for n in sim}
    grand_totals = {n: sim[n]["total"] for n in sim}

    print()
    print("  GRAND TOTAL (what the run actually consumes) -- THIS is the invariant")
    uniq_grand = set(grand_totals.values())
    if len(uniq_grand) == 1:
        print(f"    all three spend exactly {grand_totals['direct']} turns -> FAIR")
        print()
        print("    The reserve is carved out of the pool, not added on top, so a")
        print("    review win cannot be explained by a larger budget. The question")
        print("    the experiment answers is: given the same turns, which strategy")
        print("    does best?")
    else:
        print(f"    UNEQUAL: {grand_totals}")
        spread = max(grand_totals.values()) - min(grand_totals.values())
        print(f"    a {spread}-turn advantage would confound any strategy comparison")

    print()
    print("  BASE FLOW (expected to differ -- review pays for its own revision)")
    uniq_base = set(base_totals.values())
    if len(uniq_base) == 1:
        print(f"    all three spend {base_totals['direct']} on base acts")
    else:
        print(f"    {base_totals}")
        print(f"    review's base flow is smaller by the reserve "
              f"({grand_totals['review'] - base_totals['review']} turns): it sets that")
        print("    aside for revising, out of the same allowance. This is the")
        print("    intended trade, not a defect -- review that never revises simply")
        print("    spends less than its budget.")
        print()
        print("    CONSEQUENCE for the thesis:")
        print("      * RQ1: totals are equal, so a review WIN or LOSS is attributable")
        print("        to the strategy rather than to a bigger budget.")
        print("      * RQ2: turn/token counts are directly comparable.")
        print("      * If review never revises, its base acts had 8 turns less than")
        print("        direct's -- report that, since it is the cost of carrying a")
        print("        revision capability.")

    print()
    print("=" * 78)
    if len(uniq_grand) == 1:
        print("  FAIR: every strategy's task budget is identical.")
    else:
        print("  UNFAIR: totals differ -- do not compare strategies until this is fixed.")
    print("=" * 78)


if __name__ == "__main__":
    main()
