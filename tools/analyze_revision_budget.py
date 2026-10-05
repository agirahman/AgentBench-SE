"""Show how the revision reserve is split across revision rounds.

Answers the design question "how big should REVISION_TOOL_TURNS be?" with the
actual allocation, using the REAL ToolTurnBudget class so the numbers cannot
drift from the implementation.

The reserve is divided per round (``share_revision(2)`` for the revision act,
then ``share_revision(1)`` for the re-review that must follow it), so a reserve
that funds ONE round adequately leaves later rounds on the floor of 1 turn --
which is exactly the starvation measured on django-11001.

Usage: python tools/analyze_revision_budget.py [MAX_ROUNDS]
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.budget import ToolTurnBudget  # noqa: E402

#: Turns a revision act needs to read AND edit. MEASURED: the revision that changed a
#: patch in EXP-20260930-415 used 6 turns and made 2 edits. This file used to print a
#: recommendation of "8 * MAX_REVISION_TURNS" while computing its table with a
#: hard-coded 4 -- two different numbers, and the recommendation was the impossible
#: arithmetic behind the reserve defect itself.
USABLE_GRANT = 6


def allocate(reserve: int, rounds: int, per_act_use: int = 99,
             total: int | None = None) -> list[tuple[int, int]]:
    """Grant turns to `rounds` revision cycles the way the strategy does.

    Mirrors review_strategy: the revision act is granted ``share_revision(2 * rounds_left)``
    and the re-review that follows it ``share_revision(2 * rounds_left - 1)``, so the
    reserve is spread over every act still to come rather than front-loaded.

    ``per_act_use`` simulates how many turns each act actually spends; 99 means
    "spend the whole grant", the worst case for the reserve.

    ``total`` defaults to the CONFIGURED pool. This used to be a hard-coded 40, which
    silently changed the answer: ToolTurnBudget clamps a reserve that is >= the pool to
    pool // 4, so a reserve of 48 against a total of 40 became 10 and the table reported
    [1, 2] turns per act for a configuration that really grants 6+6. Two tools in this
    repo then disagreed about the same setting (docs/AUDIT_TEST_ROBUSTNESS.md, category 3).

    With ``reserve=0`` the legacy path is simulated faithfully: the base acts
    have already spent the whole base pool (that is what triggers a revision at
    all -- the run reached its review with the pool exhausted), so ``share()``
    returns the floor of 1. Without draining the base pool first, the legacy
    numbers look generous and hide the starvation that actually occurred.
    """
    if total is None:
        try:
            from config import Config

            total = Config.TOTAL_TOOL_TURNS or 40
        except Exception:  # noqa: BLE001
            total = 40
    b = ToolTurnBudget(total=total, revision_reserve=reserve)
    if reserve <= 0:
        b.spend(total)  # base acts used the whole pool, as in EXP-20260928-003
    out = []
    for done in range(rounds):
        rounds_left = max(1, rounds - done)
        rev = b.share_revision(2 * rounds_left)
        b.spend_revision(min(rev, per_act_use))
        re_rev = b.share_revision(max(1, 2 * rounds_left - 1))
        b.spend_revision(min(re_rev, per_act_use))
        out.append((rev, re_rev))
    return out


def main() -> None:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(f"Revision reserve split across {rounds} round(s)")
    print("(revision turns, re-review turns) per round\n")

    # 48 is the CONFIGURED reserve; it belongs in the table so the reader can see the
    # shipped value rather than infer it.
    reserves = [0, 4, 8, 12, 16, 24, 32, 48]
    print(f"{'reserve':>8s}  " + "  ".join(f"round {i+1}" for i in range(rounds)))
    print("-" * (10 + 9 * rounds))
    for r in reserves:
        alloc = allocate(r, rounds)
        cells = "  ".join(f"{rev:3d}+{rr:<3d}" for rev, rr in alloc)
        starved = sum(1 for rev, rr in alloc if rev <= 1)
        flag = "  <-- STARVED" if starved else ""
        print(f"{r:8d}  {cells}{flag}")

    print("\nLegend: 'rev+re-review' turns granted per round.")
    print("A revision granted 1 turn can read a file but cannot edit one --")
    print("measured on EXP-20260928-003 django-11001, where that cost review the instance.")

    print("\n--- turns actually needed (measured) ---")
    print("  EXP-20260929-001 smoke test, django-11001, MAX_REVISION_TURNS=1, reserve=8:")
    print("    revision act granted 4, used 4 (hit its cap), made 1 edit")
    print("    re-review  granted 4")
    print("  So ONE round costs ~4 turns per act at this instance's difficulty.")

    print("\n--- reserve needed to fund every round equally ---")
    print(f"  {'rounds':>6s} {'reserve':>8s}   per-act grant")
    for rounds_wanted in (1, 2, 3, 4):
        reserve = USABLE_GRANT * 2 * rounds_wanted
        alloc = allocate(reserve, rounds_wanted)
        grants = {rev for rev, _ in alloc}
        print(
            f"  {rounds_wanted:6d} {reserve:8d}   "
            f"{sorted(grants)}  (all rounds equal)"
        )
    # The requirement is rounds x 2 acts x USABLE_GRANT. An earlier version of this
    # line printed ">= 8 * MAX_REVISION_TURNS", which is the SAME impossible arithmetic
    # that caused the reserve defect in the first place (4 rounds x 2 acts x 8 = 64,
    # not 32) -- so the tool recommended the bug it exists to catch. Found by a partner
    # audit (docs/AUDIT_TEST_ROBUSTNESS.md, category 3).
    print(
        f"\n  Set REVISION_TOOL_TURNS >= {USABLE_GRANT} * 2 * MAX_REVISION_TURNS "
        f"(= {USABLE_GRANT} turns per act x 2 acts per round) to give every round\n"
        f"  the same {USABLE_GRANT}-turn grant that was MEASURED as sufficient: the\n"
        f"  revision that changed a patch used 6 turns and made 2 edits\n"
        f"  (EXP-20260930-415)."
    )


if __name__ == "__main__":
    main()
