"""Strategy-wide tool-turn budget.

The tool loop's cap used to be per ``act()``, which made a strategy's total an
accident of how many agents it happens to have: direct (one act) got 20 turns,
planning (two) got 40, review (three) got 60. In EXP-20260927-010 direct hit
that 20-turn cap on all three instances — it was cut off mid-exploration and
forced to answer without tools — so its numbers measured the budget rather than
the strategy, and the three strategies were not comparable.

``ToolTurnBudget`` gives every strategy the same TOTAL (``Config.TOTAL_TOOL_TURNS``)
and splits it across the acts still to come, so the base flow of each strategy
adds up to the same number:

    direct    1 act            -> 60
    planning  planner+executor -> 30 + 30
    review    plan+exec+review -> 20 + 20 + 20

A revision act (review only) draws from whatever the earlier acts left unused,
so the total stays bounded no matter how many revisions run.
"""

from dataclasses import dataclass, field

from config import Config


@dataclass
class ToolTurnBudget:
    """A strategy-wide pool of tool turns, drawn down act by act."""

    total: int
    remaining: int = field(init=False)

    def __post_init__(self) -> None:
        self.remaining = self.total

    @classmethod
    def from_config(cls) -> "ToolTurnBudget":
        return cls(total=Config.TOTAL_TOOL_TURNS)

    def share(self, acts_remaining: int) -> int:
        """Turns to grant the next act, split evenly across the acts still to come.

        ``acts_remaining`` counts the act about to run plus every act that still
        needs budget, which is what makes the base flow add up to ``total``
        exactly (e.g. review: 60//3, 40//2, 20//1 = 20+20+20).
        """
        if self.total <= 0:
            # Legacy behaviour: no strategy-wide pool, just the per-act cap, so
            # pre-budget runs stay reproducible via TOTAL_TOOL_TURNS=0.
            return Config.MAX_TOOL_TURNS
        if self.remaining <= 0:
            # Never grant zero: an act that cannot make a single call cannot
            # produce an answer at all, which would be a silent failure.
            return 1
        return max(1, self.remaining // max(1, acts_remaining))

    def spend(self, turns: int) -> None:
        """Record what an act actually cost, freeing the unused remainder."""
        self.remaining -= max(0, turns)
