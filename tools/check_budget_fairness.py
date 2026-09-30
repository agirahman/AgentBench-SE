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


def simulate(
    total: int,
    floor: int,
    reserve: int,
    mode: str = "per_task",
    rounds_allowed: int = 1,
) -> dict:
    """Run each strategy's act sequence and report what it drew.

    Every act takes its whole grant (the worst case for fairness: nobody leaves
    anything behind), which is what actually happened in the recorded runs -- the
    truncation warnings show acts using their full allowance.

    ``with_revisions`` mirrors the callers: only review has revision acts, so only
    review carves the reserve out of its pool. Requesting it for direct or planning
    would cost them turns for acts they never run.

    ``rounds_allowed`` is ``MAX_REVISION_TURNS``: how many revise-and-re-review
    rounds review may run. It changes the per-round grant even when the reserve and
    the total are unchanged, so a check that hard-codes one round cannot see it.
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

    # review: planner, executor, reviewer, then the revision rounds the strategy is
    # actually allowed to run if the reviewer keeps rejecting.
    #
    # Simulating ONE round was a blind spot: the reserve is divided across every act
    # still to come, so the grant per round depends on how many rounds are allowed. A
    # config with reserve=32 and 1 round grants 16+16, but the same reserve with 4
    # rounds grants 4+4 -- below the measured need to complete an edit. The fairness
    # check passed either way because it only looked at the total. Found by a partner
    # audit (docs/AUDIT_SCALE_PARTNER.md, area 4); rounds is now an input.
    b = ToolTurnBudget(total=total, floor=floor, mode=mode, revision_reserve=reserve)
    acts = []
    for acts_to_come in (3, 2, 1):
        grant = b.share(acts_to_come)
        b.spend(grant)
        acts.append(grant)
    base_total = sum(acts)
    revision_grants = []
    rounds = max(1, rounds_allowed)
    for i in range(rounds):
        left = max(1, rounds - i)
        for acts_remaining in (2 * left, max(1, 2 * left - 1)):
            grant = b.share_revision(acts_remaining)
            b.spend_revision(grant)
            revision_grants.append(grant)
    out["review"] = {
        "acts": acts,
        "revision_acts": revision_grants,
        "base_total": base_total,
        "rounds": rounds,
        "total": base_total + sum(revision_grants),
    }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--total", type=int, default=None)
    parser.add_argument("--floor", type=int, default=None)
    parser.add_argument("--reserve", type=int, default=None,
                        help="REVISION_TOOL_TURNS (0 = the old starved behaviour)")
    parser.add_argument("--mode", default=None)
    parser.add_argument("--rounds", type=int, default=None,
                        help="MAX_REVISION_TURNS; defaults to the configured value, "
                             "because hard-coding 1 hid a config where the reserve "
                             "was sized for 4 rounds and only 1 could run")
    args = parser.parse_args()

    # Default to the CONFIGURED values. Defaulting to the old 40/8/1 made this check
    # report on a configuration nobody was going to run.
    try:
        from config import Config
        cfg = {
            "total": Config.TOTAL_TOOL_TURNS,
            "floor": Config.BUDGET_FLOOR_PER_ACT,
            "reserve": Config.REVISION_TOOL_TURNS,
            "mode": Config.BUDGET_MODE,
            "rounds": Config.MAX_REVISION_TURNS,
        }
    except Exception:  # noqa: BLE001
        cfg = {"total": 40, "floor": 10, "reserve": 8, "mode": "per_task", "rounds": 1}

    args.total = cfg["total"] if args.total is None else args.total
    args.floor = cfg["floor"] if args.floor is None else args.floor
    args.reserve = cfg["reserve"] if args.reserve is None else args.reserve
    args.mode = cfg["mode"] if args.mode is None else args.mode
    rounds = cfg["rounds"] if args.rounds is None else args.rounds

    print("=" * 78)
    print(f"  BUDGET FAIRNESS -- total={args.total} floor={args.floor} "
          f"reserve={args.reserve} mode={args.mode} rounds={rounds}")
    print("=" * 78)

    sim = simulate(args.total, args.floor, args.reserve, args.mode, rounds)

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

    # Per-round grants. Equal TOTALS can still hide a review arm that cannot revise:
    # the reserve is divided across every act still to come, so the same reserve
    # yields 16+16 for one round and 4+4 for four. A 4-turn act reads and runs out.
    review = sim["review"]
    grants = review.get("revision_acts") or []
    print()
    print("  REVISION GRANTS (per act, in order)")
    if not grants:
        print("    no revision rounds allowed -- review would run WITHOUT revision")
    else:
        pairs = [f"{grants[i]}+{grants[i + 1]}" for i in range(0, len(grants) - 1, 2)]
        print(f"    rounds={review['rounds']}  " + "  ".join(pairs))
        smallest = min(grants)
        if smallest < 4:
            print(f"    SMALLEST GRANT = {smallest} -- an act with fewer than 4 turns")
            print("    reads files and runs out before editing, so the round is")
            print("    decorative. Raise REVISION_TOOL_TURNS or lower the round count.")

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
    starved = bool(grants) and min(grants) < 4
    if starved:
        print("  UNFAIR: a revision act gets fewer than 4 turns, so review cannot")
        print("          revise even though its budget is nominally equal. Equal")
        print("          TOTALS are not enough -- the reserve must also be usable.")
    elif len(uniq_grand) == 1:
        print("  FAIR: every strategy's task budget is identical, and every revision")
        print("        act can both read and edit.")
    else:
        print("  UNFAIR: totals differ -- do not compare strategies until this is fixed.")
    print("=" * 78)


if __name__ == "__main__":
    main()
