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


def test_review_strategy_revision_adds_executor_and_re_review(issue):
    """One revision turn costs two inferences: the executor's rewrite AND a
    re-review of it.

    Re-reviewing is the point: without it the strategy shipped whichever diff the
    working tree held last, so a rejected rewrite replaced a working patch with
    nothing checking it (EXP-20260927-007, django-10924).
    """
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
