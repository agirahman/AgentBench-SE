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

    direct    1 act            -> 40
    planning  planner+executor -> 20 + 20
    review    plan+exec+review -> 13 + 13 + 14

Two pools, because a revision is not part of the base flow:

* **base pool** — ``total``, split across the base acts. Every strategy's base
  flow adds up to exactly this, which is what makes the comparison fair.
* **revision pool** — ``revision_reserve``, only ever drawn by review's revision
  acts. It defaults to 0 (legacy behaviour: revisions draw the base remainder
  and get a floor of 1 turn).

The separate pool exists because of what EXP-20260928-003 measured. Review's
base acts spent the whole base pool, so the revision act was granted the floor
of **1 turn** on django-11001 — enough to read two files, not enough to edit
one. The reviewer had diagnosed the bug correctly ("the real fix is to compile
the pattern with ``re.DOTALL``") and returned NEEDS_REVISION; with no approved
candidate the strategy shipped the rejected initial patch. direct and planning,
which have no revision overhead, both shipped ``re.DOTALL`` and resolved.

Charging the revision to the base pool made review's extra act a punishment: it
could not act on its own review. The reserve keeps the base flow equal across
strategies (so the comparison is still like-for-like) while giving the revision
act a usable allowance. Any run using it must report the extra turns, since
review's total is then ``total + revision_reserve``.
"""

from dataclasses import dataclass, field

from config import Config


@dataclass
class ToolTurnBudget:
    """A strategy-wide pool of tool turns, drawn down act by act."""

    total: int
    revision_reserve: int = 0
    remaining: int = field(init=False)
    revision_remaining: int = field(init=False)

    def __post_init__(self) -> None:
        self.remaining = self.total
        # The reserve is a separate allowance, not a slice of ``total``: the
        # base flow must stay equal across strategies or the comparison stops
        # being like-for-like.
        self.revision_remaining = max(0, self.revision_reserve)

    @classmethod
    def from_config(cls) -> "ToolTurnBudget":
        return cls(
            total=Config.TOTAL_TOOL_TURNS,
            revision_reserve=Config.REVISION_TOOL_TURNS,
        )

    def share(self, acts_remaining: int) -> int:
        """Turns to grant the next base act, split evenly across those to come.

        ``acts_remaining`` counts the act about to run plus every act that still
        needs budget, which is what makes the base flow add up to ``total``
        exactly (e.g. review: 40//3, 27//2, 14//1 = 13+13+14).
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

    def share_revision(self, acts_remaining: int) -> int:
        """Turns to grant the next revision act, from the reserve only.

        With no reserve configured this is the old behaviour — the base
        remainder, which is usually the floor of 1 — so historical runs stay
        reproducible.
        """
        if self.revision_reserve <= 0:
            return self.share(acts_remaining)
        if self.revision_remaining <= 0:
            return 1
        return max(1, self.revision_remaining // max(1, acts_remaining))

    def spend(self, turns: int) -> None:
        """Record what a base act actually cost, freeing the unused remainder."""
        self.remaining -= max(0, turns)

    def spend_revision(self, turns: int) -> None:
        """Record what a revision act cost, drawing down the reserve."""
        if self.revision_reserve <= 0:
            # Legacy: revisions share the base pool, so this must stay
            # symmetric with share_revision's fallback.
            self.spend(turns)
            return
        self.revision_remaining -= max(0, turns)

    @property
    def revision_used(self) -> int:
        """Turns actually spent from the reserve (for reporting)."""
        return max(0, self.revision_reserve - self.revision_remaining)
