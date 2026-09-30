"""Strategy-wide tool-turn budget.

The tool loop's cap used to be per ``act()``, which made a strategy's total an
accident of how many agents it happens to have: direct (one act) got 20 turns,
planning (two) got 40, review (three) got 60. In EXP-20260927-010 direct hit
that 20-turn cap on all three instances — it was cut off mid-exploration and
forced to answer without tools — so its numbers measured the budget rather than
the strategy, and the three strategies were not comparable.

``ToolTurnBudget`` gives every strategy the same BASE TOTAL (``Config.TOTAL_TOOL_TURNS``)
and splits it across the base acts, so the base flow of each strategy adds up to
the same number:

    direct    1 act            -> 40
    planning  planner+executor -> 20 + 20
    review    plan+exec+review -> 13 + 13 + 14

Two pools, because a revision is not part of the base flow:

* **base pool** — ``total``, split across the base acts. Every strategy's base
  flow adds up to exactly this, which is what makes the comparison fair.
* **revision pool** — ``revision_reserve``, only ever drawn by review's revision
  acts. It defaults to 0 (legacy behaviour: revisions draw the base remainder
  and get a floor of 1 turn).

⚠️ **The GRAND total is therefore NOT equal — only the base flow is.** With
``total=40`` and ``revision_reserve=8``:

    direct     40 base              -> 40
    planning   40 base              -> 40
    review     40 base + 8 revision -> 48

The reserve is an extra allowance, not a slice of the base pool, so review may
consume 20% more turns. That is a real confound for comparing the three
strategies (RQ1 effectiveness, RQ2 efficiency) and has to be REPORTED, not
assumed away. ``tools/check_budget_fairness.py`` prints the numbers and the three
options: accept it and state it, raise ``total`` for direct/planning to match, or
keep ``reserve=0`` and admit the review arm cannot revise at all.

The separate pool exists because of what EXP-20260928-003 measured. Review's
base acts spent the whole base pool, so the revision act was granted the floor
of **1 turn** on django-11001 — enough to read two files, not enough to edit
one. The reviewer had diagnosed the bug correctly ("the real fix is to compile
the pattern with ``re.DOTALL``") and returned NEEDS_REVISION; with no approved
candidate the strategy shipped the rejected initial patch. direct and planning,
which have no revision overhead, both shipped ``re.DOTALL`` and resolved.

Charging the revision to the base pool made review's extra act a punishment: it
could not act on its own review. The reserve keeps the base flow equal across
strategies while giving the revision act a usable allowance. Any run using it
must report the extra turns, since review's total is then ``total +
revision_reserve``.

``mode="per_task"`` — the reference-compatible rule
---------------------------------------------------

``mode="per_act"`` (default) recomputes an even split of the remainder for each
act: ``grant = remaining // acts_to_come``. It CAPS the first act (13 of 40 for
review) and leaves the later acts whatever is left, which can be the floor of 1 —
observed twice in EXP-20260928-003 when a revision act was granted 1 turn.

``mode="per_task"`` gives every act the whole remainder minus a reserve held for
the acts still to come:

    grant = remaining - floor * acts_still_to_come

so each later act is GUARANTEED ``floor`` turns while the current act may draw the
rest. This is the structure every reference implementation uses (mini-SWE-agent
``step_limit``, SWE-agent cost cap, OpenHands ``max_iterations``, SWE-bench Pro:
all per task, none per agent), which is why it is the mode to use for a
reference-comparable run.

The two rules never coincide: with pool 40 and 3 acts, per_act gives 13/13/14 while
per_task with floor 13 gives 14/13/13 (verified in tools/analyze_pool_vs_reference.py).
Measured on EXP-003, the first act is not a hog — review's planner takes 27% of the
run's tool calls, less than the reviewer — so a free draw does not starve the tail
in practice; the floor is cheap insurance, not a load-bearing rule.
"""

from dataclasses import dataclass, field
import warnings

from config import Config


@dataclass
class ToolTurnBudget:
    """A strategy-wide pool of tool turns, drawn down act by act."""

    total: int
    revision_reserve: int = 0
    floor: int = 0
    mode: str = "per_act"
    remaining: int = field(init=False)
    revision_remaining: int = field(init=False)
    cost_remaining: float = field(init=False)

    def __post_init__(self) -> None:
        self.remaining = self.total
        # The reserve is a separate allowance, not a slice of ``total``: the
        # base flow must stay equal across strategies or the comparison stops
        # being like-for-like.
        self.revision_remaining = max(0, self.revision_reserve)
        self.cost_remaining = max(0.0, Config.COST_LIMIT_USD)
        # A per_task pool without a revision reserve is a trap, not a setting:
        # the base flow consumes the pool by design, so every revision is
        # silently granted the floor of 1 turn and cannot edit. That failure
        # looked like a finding ("the patch was already correct") for two
        # experiments before it was measured. Warn instead of discovering it
        # again from a 150-run sweep.
        if self.mode == "per_task" and self.total > 0 and self.revision_reserve <= 0:
            warnings.warn(
                "BUDGET_MODE=per_task with REVISION_TOOL_TURNS=0: the base acts "
                "consume the whole pool, so any revision act will be granted the "
                "floor of 1 turn and cannot edit. Set REVISION_TOOL_TURNS >= 8 "
                "per allowed round (MAX_REVISION_TURNS) for a functional review "
                "arm. This warning is expected for historical-run reproduction.",
                UserWarning,
                stacklevel=2,
            )

    @classmethod
    def from_config(cls) -> "ToolTurnBudget":
        return cls(
            total=Config.TOTAL_TOOL_TURNS,
            revision_reserve=Config.REVISION_TOOL_TURNS,
            floor=Config.BUDGET_FLOOR_PER_ACT,
            mode=Config.BUDGET_MODE,
        )

    def share(self, acts_remaining: int) -> int:
        """Turns to grant the next base act.

        ``acts_remaining`` counts the act about to run plus every act that still
        needs budget.

        per_act (default): ``remaining // acts_remaining``, which makes the base
        flow add up to ``total`` exactly (review: 40//3, 27//2, 14//1 = 13+13+14).

        per_task: ``remaining - floor * (acts_remaining - 1)``, so the acts still
        to come keep their floor and this act may draw the rest.
        """
        if self.total <= 0:
            # Legacy behaviour: no strategy-wide pool, just the per-act cap, so
            # pre-budget runs stay reproducible via TOTAL_TOOL_TURNS=0.
            return Config.MAX_TOOL_TURNS
        if self.remaining <= 0:
            # Never grant zero: an act that cannot make a single call cannot
            # produce an answer at all, which would be a silent failure.
            return 1
        if self.mode == "per_task":
            acts_after = max(0, acts_remaining - 1)
            return max(1, self.remaining - acts_after * max(0, self.floor))
        return max(1, self.remaining // max(1, acts_remaining))

    def share_revision(self, acts_remaining: int) -> int:
        """Turns to grant the next revision act, from the reserve only.

        ``acts_remaining`` must count EVERY revision act still to run, including
        those in later rounds. Each round costs two acts (the revision and the
        re-review that must follow it), so a loop allowing ``R`` rounds has at
        most ``2 * R`` revision acts left.

        Passing only the current round's 2 acts front-loads the whole reserve
        into round 1 and leaves later rounds on the floor of 1 turn — the same
        starvation this reserve exists to fix, just moved to round 2. Measured
        with tools/analyze_revision_budget.py: reserve=8 with 3 rounds allowed
        grants 4+4 to round 1 and 1+1 to rounds 2 and 3.

        The reserve is honoured in BOTH modes. ``per_task`` used to ignore it on
        the theory that "the pool is large enough that a revision draws from it
        like any other act". That theory was wrong, and measurably so: the base
        flow is DESIGNED to consume the whole pool — the last base act is granted
        ``share(1)``, i.e. everything left — so by the time a revision runs,
        ``remaining`` is 0 and ``share()`` returns the floor of 1 turn. Measured
        in EXP-20260929-003 (django-11019/review: "Tool loop hit
        max_tool_turns=1 for role=executor", revision made 0 edits) and again in
        EXP-20260929-022. A revision granted one turn can make one tool call; if
        that call is a read, no edit is possible and the round is decorative.

        With no reserve configured this is the old behaviour — the base
        remainder, usually the floor of 1 — so historical runs stay reproducible.
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

    def cost_share(self, acts_remaining: int) -> float | None:
        """Dollar allowance for the next act, drawn from the task's cost cap.

        The reference implementations bound a whole task by dollars ($3), so the
        cap must be split across acts the same way turns are -- otherwise the
        first act could spend the entire task allowance and the later acts would
        run uncapped, which is the opposite of a task-level bound.

        Three distinct outcomes, and the caller must not conflate them:

        * ``None``  -- no cap configured, so the act runs unbounded.
        * ``0.0``   -- the cap is EXHAUSTED; the act must not spend at all.
        * ``> 0``   -- this act's share of what remains.

        Returning 0.0 for the disabled case would be a silent hole: callers test
        truthiness to mean "no limit", so an exhausted budget would read as
        unlimited and the guard would vanish exactly when it is needed.
        """
        if Config.COST_LIMIT_USD <= 0:
            return None
        if self.cost_remaining <= 0:
            return 0.0
        if acts_remaining <= 1:
            return self.cost_remaining
        return max(0.0, self.cost_remaining / max(1, acts_remaining))

    def spend_cost(self, usd: float) -> None:
        """Record what an act actually cost, freeing the unused allowance."""
        self.cost_remaining -= max(0.0, usd)
