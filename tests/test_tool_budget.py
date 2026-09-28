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
