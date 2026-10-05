import pytest

from models.issue import Issue
from strategies.direct_strategy import DirectStrategy
from strategies.planning_strategy import PlanningStrategy
from strategies.review_strategy import ReviewStrategy


class DummyInference:
    def __init__(self, response, role):
        self.response = response
        self.role = role
        self.usage = {}
        self.finish_reason = "STOP"
        self.model = "demo-model"
        self.prompt_tokens = 0
        self.cached_tokens = 0
        self.regular_input_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.timestamp = ""
        # Strategies debit a task-level dollar budget with this. A double must
        # carry it or the strategy blows up on a stub that is otherwise faithful.
        self.cost_usd = 0.0


class DummyProvider:
    def __init__(self, reviewer_verdict='{"verdict": "APPROVED"}'):
        self.model = "demo-model"
        self.calls = []
        self.reviewer_verdict = reviewer_verdict

    def generate(self, prompt, role=""):
        self.calls.append((role, prompt))
        if role == "planner":
            return DummyInference("plan", role)
        if role == "reviewer":
            return DummyInference(self.reviewer_verdict, role)
        return DummyInference("patch", role)


@pytest.fixture
def issue():
    return Issue(
        instance_id="ISSUE-1",
        repo="django/django",
        base_commit="abc123",
        problem_statement="Bug",
    )


def test_direct_strategy_uses_only_direct_agent(issue):
    provider = DummyProvider()
    patch, result = DirectStrategy(provider).run(issue)

    assert patch.response == "patch"
    assert result.execution.inference_count == 1
    assert [inf.role for inf in result.execution.inferences] == ["direct"]


def test_planning_strategy_chains_planner_then_executor(issue):
    provider = DummyProvider()
    patch, result = PlanningStrategy(provider).run(issue)

    assert patch.response == "patch"
    assert result.execution.inference_count == 2
    assert [inf.role for inf in result.execution.inferences] == ["planner", "executor"]


def test_review_strategy_approved_uses_three_agents(issue):
    provider = DummyProvider()
    patch, result = ReviewStrategy(provider).run(issue)

    assert patch.response == "patch"
    assert result.execution.inference_count == 3
    assert [inf.role for inf in result.execution.inferences] == ["planner", "executor", "reviewer"]


def test_review_strategy_revision_adds_executor_and_re_review(issue, monkeypatch):
    """One revision turn costs two inferences: the executor's rewrite AND a
    re-review of it.

    Re-reviewing is the point: without it the strategy shipped whichever diff the
    working tree held last, so a rejected rewrite replaced a working patch with
    nothing checking it (EXP-20260927-007, django-10924).

    MAX_REVISION_TURNS is PINNED to 1. Left to the ambient environment this test
    passed only while the shell happened to export 1; with the configured value of 4
    the loop correctly runs four rounds and the assertion below (5 inferences) is
    simply the wrong expectation. A test whose outcome depends on the caller's
    environment is not testing the code.
    """
    from agents import base as base_mod
    from agents import budget as budget_mod
    from agents import registry as registry_mod
    from agents import tools as tools_mod
    from strategies import review_strategy

    for mod in (base_mod, budget_mod, registry_mod, tools_mod, review_strategy):
        monkeypatch.setattr(mod.Config, "MAX_REVISION_TURNS", 1)

    provider = DummyProvider(reviewer_verdict='{"verdict": "NEEDS_REVISION"}')
    patch, result = ReviewStrategy(provider).run(issue)

    assert patch.response == "patch"
    assert result.execution.inference_count == 5
    assert [inf.role for inf in result.execution.inferences] == [
        "planner", "executor", "reviewer", "executor", "reviewer",
    ]


class BudgetRecordingProvider(DummyProvider):
    """Records the max_tool_turns each agent was granted.

    The returned inference reports ``api_turns`` equal to the granted amount, so
    the run simulates an agent that uses its whole budget. That is the worst case
    for the pool, and it makes the granted totals add up to the strategy's total
    exactly (a real run that stops early rolls the remainder forward instead).
    """

    def __init__(self, reviewer_verdict='{"verdict": "APPROVED"}'):
        super().__init__(reviewer_verdict)
        self.tool_turns: list[tuple[str, int]] = []

    def generate_with_tools(self, prompt, role="", tools=None, max_tool_turns=None, **kwargs):
        self.tool_turns.append((role, max_tool_turns))
        inference = self.generate(prompt, role)
        inference.api_turns = max_tool_turns or 1
        return inference


@pytest.fixture
def tool_provider(monkeypatch):
    """A provider that exposes generate_with_tools, so tool calling activates."""
    # Patch every module that holds its own `from config import Config` binding.
    # test_response_utils reloads the config module, which rebinds config.Config
    # to a NEW class object while modules that already imported it keep the old
    # one — so patching only "config.Config" would miss the object the
    # strategies actually read. That is how these tests passed before: the
    # ambient shell happened to carry TOTAL_TOOL_TURNS=60, matching the number
    # the test asserted, so the assertion never really exercised .env.
    #
    # strategies.direct_strategy and planning_strategy are absent on purpose:
    # they never import Config, they go through ToolTurnBudget.from_config(),
    # which reads agents.budget.Config — patched below.
    from agents import base as base_mod
    from agents import budget as budget_mod
    from agents import registry as registry_mod
    from agents import tools as tools_mod
    from strategies import review_strategy

    for mod in (base_mod, budget_mod, registry_mod, tools_mod, review_strategy):
        monkeypatch.setattr(mod.Config, "TOOLCALL_ENABLED", True)
        monkeypatch.setattr(mod.Config, "TOTAL_TOOL_TURNS", 40)
        monkeypatch.setattr(mod.Config, "REVISION_TOOL_TURNS", 0)
    monkeypatch.setattr(
        "agents.base.load_prompt_or_default",
        lambda filename, default="": str(default),
    )
    return BudgetRecordingProvider()


def test_every_strategy_gets_the_same_total_tool_turns(issue, tool_provider):
    """The base flow of all three strategies must add up to the same total.

    Before this, the per-act cap gave direct 20, planning 40 and review 60 —
    direct hit its cap on all three EXP-20260927-010 instances and was cut off
    mid-exploration, so the comparison measured the budget, not the strategy.

    The total is the configured 40, not a number baked into the test: the point
    is that all three are EQUAL, whatever the run is configured with.
    """
    totals = {}
    for name, strategy_cls in (
        ("direct", DirectStrategy),
        ("planning", PlanningStrategy),
        ("review", ReviewStrategy),
    ):
        provider = BudgetRecordingProvider()
        strategy_cls(provider).run(issue)
        totals[name] = sum(granted for _role, granted in provider.tool_turns)

    assert totals["direct"] == 40, totals
    assert totals["planning"] == 40, totals
    assert totals["review"] == 40, totals
    assert len(set(totals.values())) == 1, f"budgets differ: {totals}"


def test_direct_is_not_starved_by_its_single_agent(issue, tool_provider):
    """direct's one act must receive the whole pool, not a fraction of it."""
    provider = BudgetRecordingProvider()
    DirectStrategy(provider).run(issue)
    assert provider.tool_turns == [("direct", 40)]


def test_revision_act_gets_a_usable_share_from_the_reserve(issue, monkeypatch):
    """The measured failure: review's revision act was granted 1 turn.

    EXP-20260928-003, django-11001. The base flow spent the whole pool, so
    ``budget.share(2)`` for the revision returned the floor of 1. The revision
    made two read_file calls and no edit, the re-review rejected the unchanged
    patch, and review shipped the initial (rejected) patch while direct and
    planning both resolved the issue with ``re.DOTALL``.

    This pins the fix end-to-end through the strategy: with a reserve the
    revision act must be granted more than the floor.

    The reserve is carved OUT of the task pool, so the base acts total 40 - 8 =
    32 here and review's whole task still costs 40 turns -- the same as direct and
    planning. The earlier design kept the base flow at 40 and let the reserve be
    extra, which gave review 48 turns and confounded the comparison.

    MAX_REVISION_TURNS is PINNED to 1 so the reserve-8 figure means "8 turns for the
    one round". The reserve is divided across every revision act still to come, so
    the same 8 with the configured 4 rounds grants 8 // (2 x 4) = 1 turn per act --
    starved. That combination is exactly what the sweep would have run, and it is
    what test_a_small_reserve_with_many_rounds_starves_every_round pins below.
    Pinning here keeps each test about ONE variable.
    """
    from agents import base as base_mod
    from agents import budget as budget_mod
    from agents import registry as registry_mod
    from agents import tools as tools_mod
    from strategies import review_strategy

    for mod in (base_mod, budget_mod, registry_mod, tools_mod, review_strategy):
        monkeypatch.setattr(mod.Config, "TOOLCALL_ENABLED", True)
        monkeypatch.setattr(mod.Config, "TOTAL_TOOL_TURNS", 40)
        monkeypatch.setattr(mod.Config, "REVISION_TOOL_TURNS", 8)
        monkeypatch.setattr(mod.Config, "MAX_REVISION_TURNS", 1)
    monkeypatch.setattr(
        "agents.base.load_prompt_or_default",
        lambda filename, default="": str(default),
    )

    provider = BudgetRecordingProvider(reviewer_verdict='{"verdict": "NEEDS_REVISION"}')
    ReviewStrategy(provider).run(issue)

    granted = {role: [] for role, _ in provider.tool_turns}
    for role, turns in provider.tool_turns:
        granted.setdefault(role, []).append(turns)

    base_total = granted["planner"][0] + granted["executor"][0] + granted["reviewer"][0]
    assert base_total == 32, (
        f"review's base flow must be 40 - 8 reserve = 32, got {base_total}"
    )

    # The revision act is the executor's SECOND call; the re-review follows it.
    revision_grant = granted["executor"][1]
    assert revision_grant > 1, (
        f"revision was granted {revision_grant} turn(s) — the floor that starved "
        f"django-11001; it cannot edit with that"
    )

    # The whole task must still fit the budget the other strategies get.
    total_granted = base_total + revision_grant
    re_review_grant = granted["reviewer"][1] if len(granted["reviewer"]) > 1 else 0
    total_granted += re_review_grant
    assert total_granted <= 40, (
        f"review was granted {total_granted} turns in total, above the 40 every "
        f"strategy is supposed to get"
    )


def test_every_revision_round_gets_a_usable_grant(issue, monkeypatch):
    """With several rounds allowed, no round may fall to the floor of 1 turn.

    Funding only the current round front-loads the reserve, so round 2 onward
    cannot edit — the same starvation the reserve was added to fix, just moved
    later. The reserve is sized here for 3 rounds (24 = 3 rounds x 2 acts x 4).
    """
    from agents import base as base_mod
    from agents import budget as budget_mod
    from agents import registry as registry_mod
    from agents import tools as tools_mod
    from strategies import review_strategy

    for mod in (base_mod, budget_mod, registry_mod, tools_mod, review_strategy):
        monkeypatch.setattr(mod.Config, "TOOLCALL_ENABLED", True)
        monkeypatch.setattr(mod.Config, "TOTAL_TOOL_TURNS", 40)
        monkeypatch.setattr(mod.Config, "REVISION_TOOL_TURNS", 24)
        monkeypatch.setattr(mod.Config, "MAX_REVISION_TURNS", 3)
    monkeypatch.setattr(
        "agents.base.load_prompt_or_default",
        lambda filename, default="": str(default),
    )

    # Always rejects, so all 3 rounds actually run.
    provider = BudgetRecordingProvider(reviewer_verdict='{"verdict": "NEEDS_REVISION"}')
    ReviewStrategy(provider).run(issue)

    executor_grants = [t for role, t in provider.tool_turns if role == "executor"]
    assert len(executor_grants) == 4, (
        f"expected 1 base + 3 revision acts, got {executor_grants}"
    )
    revision_grants = executor_grants[1:]
    assert all(g > 1 for g in revision_grants), (
        f"a revision round was left unable to edit: {revision_grants}"
    )
    assert len(set(revision_grants)) == 1, (
        f"rounds got unequal grants, so later rounds are penalised: {revision_grants}"
    )


def test_a_small_reserve_with_many_rounds_starves_every_round(issue, monkeypatch):
    """The configuration the sweep would have run, pinned as the FAILURE it is.

    REVISION_TOOL_TURNS=8 with MAX_REVISION_TURNS=4 gives review a correct total and
    a correct number of rounds, and still cannot revise: the reserve is divided
    across every revision act still to come, so each of the 8 acts (4 rounds x 2)
    gets 8 // 8 = 1 turn. One turn reads a file; it cannot edit one.

    Every other check passed on this shape -- the totals were equal (200/200/200)
    and the round count was the intended one -- which is why the failure survived
    408 tests. This test makes the gap between "the budget is fair" and "the budget
    is usable" explicit.
    """
    from agents import base as base_mod
    from agents import budget as budget_mod
    from agents import registry as registry_mod
    from agents import tools as tools_mod
    from strategies import review_strategy

    for mod in (base_mod, budget_mod, registry_mod, tools_mod, review_strategy):
        monkeypatch.setattr(mod.Config, "TOOLCALL_ENABLED", True)
        monkeypatch.setattr(mod.Config, "TOTAL_TOOL_TURNS", 200)
        monkeypatch.setattr(mod.Config, "REVISION_TOOL_TURNS", 8)
        monkeypatch.setattr(mod.Config, "MAX_REVISION_TURNS", 4)
    monkeypatch.setattr(
        "agents.base.load_prompt_or_default",
        lambda filename, default="": str(default),
    )

    provider = BudgetRecordingProvider(reviewer_verdict='{"verdict": "NEEDS_REVISION"}')
    ReviewStrategy(provider).run(issue)

    executor_grants = [t for role, t in provider.tool_turns if role == "executor"]
    revision_grants = executor_grants[1:]
    assert revision_grants, "four rounds were allowed, so revision acts must run"
    assert all(g == 1 for g in revision_grants), (
        f"this shape is supposed to starve every round; got {revision_grants}. "
        f"If the split changed, re-derive the numbers rather than deleting the check"
    )


def test_the_configured_reserve_can_fund_every_configured_round(issue, monkeypatch):
    """The shipped configuration must NOT be the starved shape above.

    Reads the values the sweep actually passes (REVISION_TOOL_TURNS=48,
    MAX_REVISION_TURNS=4) and asserts every revision act gets enough turns to edit.
    Measured need: 6 turns (the revision that changed a patch used 6, made 2 edits).
    """
    from agents import base as base_mod
    from agents import budget as budget_mod
    from agents import registry as registry_mod
    from agents import tools as tools_mod
    from strategies import review_strategy

    reserve = 48
    rounds = 4
    for mod in (base_mod, budget_mod, registry_mod, tools_mod, review_strategy):
        monkeypatch.setattr(mod.Config, "TOOLCALL_ENABLED", True)
        monkeypatch.setattr(mod.Config, "TOTAL_TOOL_TURNS", 200)
        monkeypatch.setattr(mod.Config, "REVISION_TOOL_TURNS", reserve)
        monkeypatch.setattr(mod.Config, "MAX_REVISION_TURNS", rounds)
    monkeypatch.setattr(
        "agents.base.load_prompt_or_default",
        lambda filename, default="": str(default),
    )

    provider = BudgetRecordingProvider(reviewer_verdict='{"verdict": "NEEDS_REVISION"}')
    ReviewStrategy(provider).run(issue)

    executor_grants = [t for role, t in provider.tool_turns if role == "executor"]
    assert len(executor_grants) == 1 + rounds, (
        f"expected 1 base + {rounds} revision acts, got {executor_grants}"
    )
    revision_grants = executor_grants[1:]
    assert all(g >= 6 for g in revision_grants), (
        f"a revision act needs >= 6 turns to read AND edit; got {revision_grants} "
        f"from reserve={reserve} over {rounds} rounds"
    )

    # And the whole task still costs exactly what the other strategies get.
    base_total = sum(t for role, t in provider.tool_turns
                     if role in ("planner", "reviewer")) + executor_grants[0]
    revision_total = sum(revision_grants)
    assert base_total + revision_total <= 200, (
        f"review was granted {base_total + revision_total} turns, above the 200 "
        f"every strategy is supposed to get"
    )
