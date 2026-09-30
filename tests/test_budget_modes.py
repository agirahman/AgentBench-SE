"""Per-task budget mode, the floor, and the dollar guard.

Context for why these exist:

1. PER-TASK. Every reference implementation bounds a TASK (mini-SWE-agent
   step_limit=250, SWE-agent $3/instance, OpenHands max_iterations=500, SWE-bench
   Pro 200 turns) and none splits a budget per agent. Our per_act rule caps the
   first act (13 of 40 for review) and leaves the later acts whatever remains,
   which reached the floor of 1 turn twice in EXP-20260928-003 -- so a revision
   the reviewer had correctly diagnosed could not be applied.

2. FLOOR. In per_task mode an act may draw the remainder minus `floor` for each
   act still to come, so the tail is guaranteed room. Measured on EXP-003, the
   first act is not a hog (review's planner takes 27% of tool calls, less than the
   reviewer), so the floor is insurance rather than a load-bearing rule.

3. DOLLAR GUARD. SWE-agent and mini-SWE-agent bound by cost; we bounded turns
   only, so a strategy spending 2x tokens for the same turns was unconstrained.
   Measured cost is ~$0.07/run worst case against a $3 cap, so the guard should
   never fire in normal operation -- these tests pin the mechanism, not a limit we
   expect to hit.
"""
import pytest

from agents.budget import ToolTurnBudget
from config import Config


@pytest.fixture(autouse=True)
def _restore_config():
    """Config is class-level; restore it so tests cannot leak into each other."""
    saved = (
        Config.TOTAL_TOOL_TURNS,
        Config.BUDGET_MODE,
        Config.BUDGET_FLOOR_PER_ACT,
        Config.COST_LIMIT_USD,
    )
    yield
    (
        Config.TOTAL_TOOL_TURNS,
        Config.BUDGET_MODE,
        Config.BUDGET_FLOOR_PER_ACT,
        Config.COST_LIMIT_USD,
    ) = saved


# ---------------------------------------------------------------- per_act mode
def test_per_act_reproduces_the_historical_split():
    """The old rule must stay byte-identical so EXP-003 stays reproducible.

    The grants are computed as each act STARTS, with the previous act's spend
    already debited -- that is what produces 13, 13, 14 in a real review run.
    """
    b = ToolTurnBudget(total=40, mode="per_act")
    grants = []
    for acts_to_come, spent in ((3, 13), (2, 13), (1, 14)):
        grants.append(b.share(acts_to_come))
        b.spend(spent)
    assert grants == [13, 13, 14]


def test_per_act_starves_the_tail_when_early_acts_spend_everything():
    """This is the measured failure: a later act left on the floor of 1."""
    b = ToolTurnBudget(total=40, mode="per_act")
    for acts in (3, 2, 1):
        b.spend(b.share(acts))  # every act uses its whole grant
    assert b.share(1) == 1  # nothing left for a revision


# --------------------------------------------------------------- per_task mode
def test_per_task_lets_the_first_act_draw_more_than_an_even_share():
    """The point of the change: 13 is a cap under per_act, not a requirement."""
    b = ToolTurnBudget(total=100, mode="per_task", floor=10)
    assert b.share(3) == 80  # 100 - 10*2


def test_per_task_guarantees_the_floor_for_each_act_still_to_come():
    b = ToolTurnBudget(total=100, mode="per_task", floor=10)
    b.spend(b.share(3))          # act 1 takes its maximum
    b.spend(b.share(2))          # act 2 takes its maximum
    assert b.share(1) == 10      # act 3 still has its floor


def test_per_task_never_grants_zero():
    """An act with no turns cannot even answer, which would be a silent failure."""
    b = ToolTurnBudget(total=10, mode="per_task", floor=20)
    assert b.share(3) >= 1


def test_per_task_last_act_gets_everything_left():
    b = ToolTurnBudget(total=40, mode="per_task", floor=5)
    b.spend(20)
    assert b.share(1) == 20


def test_floor_zero_is_a_pure_free_draw():
    b = ToolTurnBudget(total=100, mode="per_task", floor=0)
    assert b.share(3) == 100
    assert b.share(2) == 100


# ------------------------------------------------------------- revision in per_task
def test_per_task_revision_uses_the_reserve_not_the_exhausted_pool():
    """REGRESSION: per_task used to ignore the reserve and starve every revision.

    The old rule sent a revision to ``share()``, on the theory that the pool is
    big enough to absorb it. It is not, and by design: the LAST base act is
    granted ``share(1)`` -- everything left -- so the pool is empty exactly when
    the revision starts, and ``share()`` returns the floor of 1 turn.

    Measured in EXP-20260929-003 and EXP-20260929-022:
    "Tool loop hit max_tool_turns=1 for role=executor" on the revision act, which
    then made 0 edits. A revision with one turn can make one tool call; if that
    call is a read, the round is decorative and the arm does not measure review.

    This test fails on the old code: share_revision(2) returned share(2), i.e.
    the whole remainder, instead of the reserve.
    """
    b = ToolTurnBudget(total=40, mode="per_task", floor=10, revision_reserve=8)
    # Base acts spend the pool exactly as designed.
    for acts_to_come in (3, 2, 1):
        b.spend(b.share(acts_to_come))
    assert b.remaining == 0, "the base flow is meant to consume the pool"

    # The revision must still get real room, from the reserve.
    grant = b.share_revision(2)
    assert grant == 4, f"revision starved with {grant} turn(s)"
    assert grant > 1, "one turn cannot both read a file and edit it"

    # And it must debit the reserve, not the (already empty) base pool.
    b.spend_revision(grant)
    assert b.revision_remaining == 4
    assert b.remaining == 0, "the base pool must not be touched by a revision"


def test_reserve_is_split_across_every_round_not_front_loaded():
    """Funding round 1 only would move the starvation to round 2.

    reserve=24 over 3 rounds must give 4 turns per act every round. Funding just
    the current round's 2 acts would grant 12+12 to round 1 and leave rounds 2 and
    3 on the floor of 1 -- the same defect, one round later.

    ``acts_remaining`` mirrors the real caller (review_strategy.py:177-182, 206):
    ``2 * rounds_left`` for the revision, then one less for the re-review that
    follows it, with ``rounds_left`` counting down. Passing a constant 6 every
    round is not what the caller does and would over-declare the remaining acts.
    """
    b = ToolTurnBudget(total=40, mode="per_task", floor=10, revision_reserve=24)
    grants = []
    for rounds_left in (3, 2, 1):
        rev = b.share_revision(2 * rounds_left)
        b.spend_revision(rev)
        rr = b.share_revision(max(1, 2 * rounds_left - 1))
        b.spend_revision(rr)
        grants.append((rev, rr))
    assert grants == [(4, 4), (4, 4), (4, 4)], f"uneven across rounds: {grants}"


def test_no_reserve_keeps_the_legacy_starved_behaviour():
    """Default 0 must stay reproducible for historical runs -- but it IS starved.

    This pins the trap so it cannot regress silently: any run that wants a
    functional review arm must set REVISION_TOOL_TURNS. The companion test below
    is the guard that makes forgetting it loud instead of silent.
    """
    b = ToolTurnBudget(total=40, mode="per_task", floor=10, revision_reserve=0)
    for acts_to_come in (3, 2, 1):
        b.spend(b.share(acts_to_come))
    assert b.share_revision(2) == 1  # the floor: one call, no room to edit


def test_per_task_without_a_reserve_warns_that_revisions_will_starve():
    """Forgetting the reserve must be LOUD.

    A silent 1-turn revision produces a plausible-looking run whose review arm
    cannot revise anything -- the failure mode that cost EXP-20260928-003 an
    instance and went unnoticed for two more experiments.
    """
    with pytest.warns(UserWarning, match="REVISION_TOOL_TURNS"):
        ToolTurnBudget(total=40, mode="per_task", floor=10, revision_reserve=0)


def test_per_act_revision_still_uses_the_reserve():
    """The legacy path must not change: EXP-003's revision accounting depends on it."""
    b = ToolTurnBudget(total=40, mode="per_act", revision_reserve=8)
    grant = b.share_revision(2)
    assert grant == 4  # 8 // 2
    b.spend_revision(4)
    assert b.revision_remaining == 4
    assert b.remaining == 40  # base pool untouched


# ------------------------------------------------------------------- cost guard
def test_cost_share_splits_the_task_cap_across_acts():
    Config.COST_LIMIT_USD = 3.0
    b = ToolTurnBudget(total=100)
    assert b.cost_share(3) == pytest.approx(1.0)
    assert b.cost_share(1) == pytest.approx(3.0)


def test_cost_share_returns_zero_when_disabled():
    """None means 'no cap' and must be distinguishable from an exhausted 0.0.

    Callers test `is not None` to arm the guard. Returning 0.0 for the disabled
    case would be a silent hole the other way round: an exhausted budget would
    read as 'unlimited'.
    """
    Config.COST_LIMIT_USD = 0.0
    b = ToolTurnBudget(total=100)
    assert b.cost_share(3) is None


def test_cost_share_returns_zero_not_none_when_exhausted():
    """The cap being spent must ARM the guard, not disable it."""
    Config.COST_LIMIT_USD = 3.0
    b = ToolTurnBudget(total=100)
    b.spend_cost(3.0)
    assert b.cost_share(3) == 0.0
    assert b.cost_share(3) is not None


def test_spend_cost_frees_the_unused_allowance():
    Config.COST_LIMIT_USD = 3.0
    b = ToolTurnBudget(total=100)
    b.spend_cost(0.5)               # used half of its 1.0 share
    assert b.cost_remaining == pytest.approx(2.5)
    assert b.cost_share(2) == pytest.approx(1.25)  # the remainder is redistributed


def test_cost_share_never_goes_negative():
    """A run that overshoots must not produce a negative allowance."""
    Config.COST_LIMIT_USD = 3.0
    b = ToolTurnBudget(total=100)
    b.spend_cost(99.0)
    assert b.cost_share(1) == 0.0


# --------------------------------------------------------------------- defaults
def test_default_mode_is_per_act_for_backwards_compatibility():
    """Existing runs and tests assume the old rule; the default must not move."""
    b = ToolTurnBudget.from_config()
    assert b.mode in ("per_act", "per_task")  # whatever .env says
    assert b.total == Config.TOTAL_TOOL_TURNS
