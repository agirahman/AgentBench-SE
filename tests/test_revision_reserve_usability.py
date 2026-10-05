"""The revision reserve must be USABLE, not merely equal.

Two partner audits found defects that equal turn TOTALS could not catch:

1. The reserve is divided across every revision act still to come, so the grant per
   round depends on how many rounds are allowed. ``REVISION_TOOL_TURNS=32`` with
   ``MAX_REVISION_TURNS=1`` grants 16+16 to a single round; the same reserve with 4
   rounds grants 4+4. Both give review the same total, so a fairness check that only
   compares totals passes either way -- but at 4+4 the revision act reads a file,
   runs out of turns, and never edits.

2. The round count lived only in ``.env`` and the sweep never overrode it, so the
   arm could measure ONE revision round while the reserve was documented as four.
   The configuration nobody chose was the one that would have run.

Measured need: the revision act that actually changed a patch used 6 turns
(EXP-20260930-415, django-11019 revision made 2 edits). An act given 4 used all four
reading and made none (EXP-20260929-001). These tests pin the arithmetic that keeps
every round above that line.
"""

import warnings

import pytest

from agents.budget import ToolTurnBudget


def split_rounds(reserve: int, rounds: int) -> list[tuple[int, int]]:
    """Replay the strategy's own split: (revision, re-review) per round."""
    budget = ToolTurnBudget(total=0, revision_reserve=reserve)
    out = []
    for i in range(rounds):
        left = max(1, rounds - i)
        revision = budget.share_revision(2 * left)
        budget.spend_revision(revision)
        re_review = budget.share_revision(max(1, 2 * left - 1))
        budget.spend_revision(re_review)
        out.append((revision, re_review))
    return out


def test_the_whole_reserve_is_granted_whatever_the_round_count():
    """No reserved turn may be stranded by a round count the reserve over-funds."""
    for reserve, rounds in ((48, 4), (48, 2), (32, 1), (64, 4)):
        grants = split_rounds(reserve, rounds)
        spent = sum(a + b for a, b in grants)
        assert spent == reserve, (
            f"reserve={reserve} rounds={rounds}: granted {spent}, so "
            f"{reserve - spent} turns are unreachable -- the arm under-revises "
            f"relative to what the setting says"
        )


def test_four_rounds_can_still_complete_an_edit():
    """The configured reserve must clear the MEASURED need, not just be nonzero.

    A 4-turn revision is the failure this whole reserve exists to fix: it reads,
    runs out, and the re-review then approves an unchanged patch.
    """
    configured = split_rounds(48, 4)
    smallest = min(min(a, b) for a, b in configured)
    assert smallest >= 6, (
        f"a revision act gets {smallest} turns; the measured need to complete an "
        f"edit is 6, and below that the round is decorative"
    )


def test_a_reserve_sized_for_four_rounds_is_not_enough_for_one():
    """Documents why the old value was wrong, so it is not restored.

    reserve=32 is documented in three places as "4 rounds x 8" -- arithmetically
    impossible, since 4 rounds x 2 acts x 8 = 64. At 32 the four-round split is 4+4,
    below the measured 6.
    """
    old = split_rounds(32, 4)
    assert min(min(a, b) for a, b in old) == 4, (
        "this test exists to record the old arithmetic; if the split changed, "
        "re-derive the numbers rather than deleting the check"
    )


def test_direct_and_planning_do_not_trigger_the_starvation_warning(monkeypatch):
    """The warning must describe the CONFIG, not the caller's lack of revisions.

    Direct and planning build a budget with no reserve because they run no revision
    act. The warning fired anyway, printing "REVISION_TOOL_TURNS=0" on 100 of 150
    sweep runs while the setting was 48. A warning whose own message is false is
    worse than none: it teaches the reader to ignore the case that matters.
    """
    from agents import budget as budget_mod

    monkeypatch.setattr(budget_mod.Config, "REVISION_TOOL_TURNS", 48)
    monkeypatch.setattr(budget_mod.Config, "BUDGET_MODE", "per_task")

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        budget_mod.ToolTurnBudget(total=200, revision_reserve=0, mode="per_task")


def test_the_starvation_warning_still_fires_when_the_config_is_zero(monkeypatch):
    """The opposite error: silencing it must not hide the real misconfiguration."""
    from agents import budget as budget_mod

    monkeypatch.setattr(budget_mod.Config, "REVISION_TOOL_TURNS", 0)
    monkeypatch.setattr(budget_mod.Config, "BUDGET_MODE", "per_task")

    with pytest.warns(UserWarning, match="REVISION_TOOL_TURNS=0"):
        budget_mod.ToolTurnBudget(total=200, revision_reserve=0, mode="per_task")
