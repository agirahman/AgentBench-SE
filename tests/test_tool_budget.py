"""Strategy-wide tool-turn budget.

The per-``act()`` cap made each strategy's total an accident of its agent count
(direct 20, planning 40, review 60). In EXP-20260927-010 direct hit that cap on
all three instances, so it was measured against its budget rather than against
planning and review. These tests pin the property that makes the comparison
fair: the base flow of every strategy adds up to the same total.

The tests use ``total=40``, the configured value, so a change to the split is
visible here rather than only in a run.
"""

import pytest

from agents.budget import ToolTurnBudget


def test_total_is_shared_evenly_across_acts():
    """direct/planning/review base flows must all add up to the same total."""
    # direct: a single act takes the whole pool.
    direct = ToolTurnBudget(total=40)
    direct_share = direct.share(1)
    assert direct_share == 40

    # planning: planner (2 acts remaining) then executor (1 act remaining).
    planning = ToolTurnBudget(total=40)
    planner_share = planning.share(2)
    planning.spend(planner_share)
    executor_share = planning.share(1)
    assert (planner_share, executor_share) == (20, 20)
    assert planner_share + executor_share == direct_share

    # review: planner, executor, reviewer.
    review = ToolTurnBudget(total=40)
    shares = []
    for remaining in (3, 2, 1):
        grant = review.share(remaining)
        review.spend(grant)
        shares.append(grant)
    assert shares == [13, 13, 14]
    assert sum(shares) == direct_share


def test_unused_turns_roll_over_to_later_acts():
    """An act that stops early must not waste the rest of the pool."""
    budget = ToolTurnBudget(total=40)
    planner_share = budget.share(2)  # 20
    budget.spend(5)  # planner only needed 5
    executor_share = budget.share(1)  # gets the remaining 35
    assert planner_share == 20
    assert executor_share == 35


def test_revision_draws_from_the_remainder_not_a_fresh_pool():
    """A revision act must not extend the strategy's total beyond its budget."""
    budget = ToolTurnBudget(total=40)
    for remaining in (3, 2, 1):
        budget.spend(budget.share(remaining))
    assert budget.remaining == 0

    # With nothing left, the revision still gets a floor of 1 so it can act at
    # all, but it cannot grow the pool.
    assert budget.share(2) == 1
    assert budget.remaining == 0


def test_budget_never_grants_zero_turns():
    """A zero-turn act could not make a single call and would fail silently."""
    budget = ToolTurnBudget(total=40)
    budget.spend(40)
    assert budget.share(1) >= 1
    assert budget.share(5) >= 1


def test_disabled_pool_falls_back_to_per_act_cap():
    """TOTAL_TOOL_TURNS=0 keeps the legacy per-act behaviour for old runs."""
    from config import Config

    budget = ToolTurnBudget(total=0)
    assert budget.share(3) == Config.MAX_TOOL_TURNS
    assert budget.share(1) == Config.MAX_TOOL_TURNS


def test_spend_ignores_negative_and_zero():
    budget = ToolTurnBudget(total=40)
    budget.spend(0)
    assert budget.remaining == 40
    budget.spend(-5)
    assert budget.remaining == 40


def test_base_pool_stays_equal_when_a_revision_reserve_exists():
    """The reserve must not shrink the base flow, or the comparison breaks.

    A revision is review-only; if it were paid for out of the base pool, review
    would start every run with less room than direct and planning — measuring
    the budget again, which is the bug the pool was introduced to fix.
    """
    for reserve in (0, 8, 20):
        direct = ToolTurnBudget(total=40, revision_reserve=reserve)
        assert direct.share(1) == 40

        planning = ToolTurnBudget(total=40, revision_reserve=reserve)
        planner = planning.share(2)
        planning.spend(planner)
        executor = planning.share(1)
        assert (planner, executor) == (20, 20)

        review = ToolTurnBudget(total=40, revision_reserve=reserve)
        shares = []
        for remaining in (3, 2, 1):
            grant = review.share(remaining)
            review.spend(grant)
            shares.append(grant)
        assert shares == [13, 13, 14]
        assert review.remaining == 0


def test_revision_reserve_is_not_starved_by_a_spent_base_pool():
    """The measured failure: a spent base pool left the revision act 1 turn.

    EXP-20260928-003, django-11001: the base acts used all 40 turns, so
    ``share(2)`` returned the floor of 1. The revision's two tool calls were
    both reads and no edit was made, so the reviewer's correct diagnosis
    (re.DOTALL) was never applied. With a reserve the revision can still edit.
    """
    budget = ToolTurnBudget(total=40, revision_reserve=8)
    for remaining in (3, 2, 1):
        budget.spend(budget.share(remaining))
    assert budget.remaining == 0

    # 8 reserve split across the revision and the re-review that follows it.
    revision_share = budget.share_revision(2)
    assert revision_share == 4, "a revision that can only read cannot fix anything"
    budget.spend_revision(revision_share)
    re_review_share = budget.share_revision(1)
    assert re_review_share == 4


def test_revision_reserve_does_not_extend_the_base_pool():
    """Spending the reserve must not hand turns back to the base acts."""
    budget = ToolTurnBudget(total=40, revision_reserve=8)
    budget.spend(40)
    budget.spend_revision(4)
    assert budget.remaining == 0
    assert budget.share(1) == 1
    assert budget.revision_used == 4
    assert budget.revision_remaining == 4


def test_zero_reserve_keeps_legacy_revision_behaviour():
    """REVISION_TOOL_TURNS=0 must reproduce pre-reserve runs exactly.

    Historical experiments (EXP-20260928-003) were produced this way, so the
    fallback has to draw the base remainder and spend from it, not silently
    switch to a reserve that does not exist.
    """
    budget = ToolTurnBudget(total=40, revision_reserve=0)
    for remaining in (3, 2, 1):
        budget.spend(budget.share(remaining))
    assert budget.remaining == 0
    assert budget.share_revision(2) == 1  # the floor, as measured in 11001

    budget.spend_revision(1)
    assert budget.remaining == -1, "legacy path must charge the base pool"


def test_revision_never_grants_zero_even_with_exhausted_reserve():
    budget = ToolTurnBudget(total=40, revision_reserve=2)
    budget.spend_revision(5)
    assert budget.share_revision(1) >= 1
    assert budget.share_revision(2) >= 1


def test_reserve_is_spread_over_all_remaining_rounds_not_front_loaded():
    """Later revision rounds must not be starved by an eager first round.

    The reserve is divided per act still to come. If the caller counts only the
    current round's 2 acts, round 1 takes the whole reserve and rounds 2..N are
    left on the floor of 1 turn — the same starvation the reserve was added to
    fix (django-11001 was granted 1 turn and could not edit). With 3 rounds
    allowed and a reserve of 24, every round must get the same 4-turn grant.
    """
    budget = ToolTurnBudget(total=40, revision_reserve=24)
    grants = []
    for rounds_left in (3, 2, 1):
        rev = budget.share_revision(2 * rounds_left)
        budget.spend_revision(rev)
        grants.append(rev)
        re_rev = budget.share_revision(max(1, 2 * rounds_left - 1))
        budget.spend_revision(re_rev)

    assert grants == [4, 4, 4], f"rounds got unequal grants: {grants}"
    assert all(g > 1 for g in grants), "a round was left unable to edit"


def test_reserve_sized_for_one_round_still_starves_the_rest():
    """Documents the trade-off: a reserve funds exactly as many rounds as it pays for.

    This is not a bug to fix but a bound to be aware of when choosing the value:
    with MAX_REVISION_TURNS=3, a reserve of 8 leaves rounds 2 and 3 on the floor.
    Recorded here so the number is a deliberate choice rather than a surprise.
    """
    budget = ToolTurnBudget(total=40, revision_reserve=8)
    grants = []
    for rounds_left in (3, 2, 1):
        rev = budget.share_revision(2 * rounds_left)
        budget.spend_revision(rev)
        grants.append(rev)
        budget.spend_revision(budget.share_revision(max(1, 2 * rounds_left - 1)))

    assert grants[0] == 1, "8 turns over 6 acts leaves the first round on the floor"
    assert grants == [1, 1, 2]
