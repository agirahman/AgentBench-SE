import pytest

from agentbench.core.models.issue import Issue
from agentbench.core.strategies.direct_strategy import DirectStrategy
from agentbench.core.strategies.planning_strategy import PlanningStrategy
from agentbench.core.strategies.review_strategy import ReviewStrategy


class DummyInference:
    def __init__(self, response, role):
        self.response = response
        self.role = role
        self.usage = {}
        self.finish_reason = "STOP"
        self.model = "demo-model"
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0


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


def test_review_strategy_revision_adds_executor_round(issue):
    provider = DummyProvider(reviewer_verdict='{"verdict": "NEEDS_REVISION"}')
    patch, result = ReviewStrategy(provider).run(issue)

    assert patch.response == "patch"
    assert result.execution.inference_count == 4
    assert [inf.role for inf in result.execution.inferences] == [
        "planner", "executor", "reviewer", "executor",
    ]
