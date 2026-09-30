"""What does the configured revision reserve actually buy, round by round?

`check_budget_fairness.py` verifies the TOTALS are equal across strategies, but it
simulates a single revision round. The review strategy can loop up to
``MAX_REVISION_TURNS`` rounds, and the reserve is divided across every act still to
come, so a reserve sized for 4 rounds spends differently when only 1 round runs --
the leftover turns are simply never granted, and the arm under-revises.

That mismatch was live: ``REVISION_TOOL_TURNS=32`` is documented as "4 rounds x 8"
in three places, while ``.env`` set ``MAX_REVISION_TURNS=1`` and
``run_final_sweep.py`` never overrode it. A 150-run sweep would have measured one
revision round while the config claimed four. Found by a partner audit
(docs/AUDIT_SCALE_PARTNER.md); this tool makes the gap visible before a sweep
rather than after.

Usage:
    python tools/verify_revision_rounds.py
    python tools/verify_revision_rounds.py --rounds 4 --reserve 32

Exits non-zero if the configured round count cannot be funded equally.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

#: Turns an act needs to actually edit. Measured: a revision act granted 4 turns
#: used all 4 and made 1 edit (EXP-20260929-001, django-11001); acts that edited
#: successfully in later pilots used 6-17. Below this a round is decorative.
USABLE_GRANT = 4


def split(reserve: int, rounds: int) -> list[tuple[int, int]]:
    """Replay the strategy's own arithmetic: (revision, re-review) per round.

    Mirrors review_strategy.py:189-219 -- each round grants the revision act
    ``share_revision(2 * rounds_left)`` and the re-review ``share_revision(2 *
    rounds_left - 1)``, drawing both from the reserve.
    """
    from agents.budget import ToolTurnBudget

    budget = ToolTurnBudget(0, revision_reserve=reserve)
    out: list[tuple[int, int]] = []
    for i in range(rounds):
        rounds_left = max(1, rounds - i)
        revision = budget.share_revision(2 * rounds_left)
        budget.spend_revision(revision)
        re_review = budget.share_revision(max(1, 2 * rounds_left - 1))
        budget.spend_revision(re_review)
        out.append((revision, re_review))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--reserve", type=int, default=None,
                    help="override REVISION_TOOL_TURNS")
    ap.add_argument("--rounds", type=int, default=None,
                    help="override MAX_REVISION_TURNS")
    args = ap.parse_args()

    from config import Config

    reserve = Config.REVISION_TOOL_TURNS if args.reserve is None else args.reserve
    rounds = Config.MAX_REVISION_TURNS if args.rounds is None else args.rounds
    total = Config.TOTAL_TOOL_TURNS

    print("=" * 78)
    print("  REVISION ROUNDS -- does the configured reserve fund every round?")
    print("=" * 78)
    print()
    print(f"  TOTAL_TOOL_TURNS   = {total}")
    print(f"  REVISION_TOOL_TURNS= {reserve}")
    print(f"  MAX_REVISION_TURNS = {rounds}")
    print()

    if rounds < 1:
        print("  No revision rounds are allowed at all -- the review arm would")
        print("  measure review WITHOUT revision, which is not the strategy under")
        print("  test.")
        print()
        return 1

    grants = split(reserve, rounds)
    print(f"  {'round':<7} {'revision':>9} {'re-review':>10}")
    print("  " + "-" * 30)
    for i, (rev, rr) in enumerate(grants, 1):
        flag = "" if min(rev, rr) >= USABLE_GRANT else "   <-- cannot edit"
        print(f"  {i:<7} {rev:>9} {rr:>10}{flag}")
    print()

    spent = sum(rev + rr for rev, rr in grants)
    leftover = reserve - spent
    print(f"  reserve spent: {spent} / {reserve}   unused: {leftover}")
    print()

    problems: list[str] = []

    starved = [i for i, (rev, rr) in enumerate(grants, 1) if min(rev, rr) < USABLE_GRANT]
    if starved:
        problems.append(
            f"round(s) {starved} get fewer than {USABLE_GRANT} turns per act, so the "
            f"revision cannot both read and edit"
        )

    if leftover > 0:
        problems.append(
            f"{leftover} reserved turns are never granted because only {rounds} "
            f"round(s) run -- the arm under-revises relative to what the reserve says"
        )

    # The documentation claims reserve = 8 * rounds in several places; check it.
    expected = 8 * rounds
    if reserve != expected:
        problems.append(
            f"REVISION_TOOL_TURNS={reserve} but {rounds} round(s) need >= {expected} "
            f"for a {USABLE_GRANT}-turn grant per act"
        )

    if problems:
        print("  NOT FUNDED EQUALLY:")
        for p in problems:
            print(f"    - {p}")
        print()
        print("  Fix: set REVISION_TOOL_TURNS >= 8 * MAX_REVISION_TURNS, and make the")
        print("  sweep pass both explicitly so .env cannot drift from the script.")
        print()
        return 1

    print("  OK -- every round can fund a revision that reads AND edits, and no")
    print("  reserved turn is left ungranted.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
