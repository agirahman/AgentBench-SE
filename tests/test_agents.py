import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from agentbench.core.models.issue import Issue
from agentbench.core.models.inference import InferenceResult
from agentbench.core.agents.messages import AgentMessage
from agentbench.core.agents.blackboard import Blackboard
from agentbench.core.agents.base import BaseAgent
from agentbench.core.agents.direct_agent import DirectAgent
from agentbench.core.agents.planner_agent import PlannerAgent
from agentbench.core.agents.executor_agent import ExecutorAgent
from agentbench.core.agents.reviewer_agent import ReviewerAgent
from agentbench.core.agents.registry import build_agent_team


class DummyProvider:
    def __init__(self):
        self.model = "demo-model"
        self.calls = []

    def generate(self, prompt, role=""):
        self.calls.append((role, prompt))
        return InferenceResult(
            role=role,
            response=f"resp-{role}",
            usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            execution_time=0.0,
            finish_reason="STOP",
            model=self.model,
        )


@pytest.fixture
def provider():
    return DummyProvider()


@pytest.fixture
def issue():
    return Issue(
        instance_id="ISSUE-1",
        repo="django/django",
        base_commit="abc123",
        problem_statement="Bug X",
    )


@pytest.fixture
def bb(issue):
    return Blackboard(issue=issue)


def test_direct_agent_renders_issue_and_records_message(provider, bb):
    agent = DirectAgent(provider)
    task = AgentMessage(sender="orchestrator", receiver="direct", content=bb.issue.to_prompt())

    resp = agent.act(task, bb)

    assert resp.message.sender == "direct"
    assert resp.message.receiver == "orchestrator"
    assert resp.inference.role == "direct"
    assert provider.calls[0][0] == "direct"
    assert "Bug X" in provider.calls[0][1]
    assert len(bb.history) == 1
    assert bb.history[0].sender == "direct"


def test_planner_agent_renders_issue(provider, bb):
    agent = PlannerAgent(provider)
    task = AgentMessage(sender="orchestrator", receiver="planner", content=bb.issue.to_prompt())

    resp = agent.act(task, bb)

    assert resp.inference.role == "planner"
    assert "Bug X" in provider.calls[0][1]


def test_executor_agent_uses_blackboard_plan(provider, bb):
    bb.plan = "PLAN-TEXT"
    agent = ExecutorAgent(provider)
    task = AgentMessage(sender="planner", receiver="executor", content=bb.issue.to_prompt())

    agent.act(task, bb)

    prompt = provider.calls[0][1]
    assert "PLAN-TEXT" in prompt
    assert "Bug X" in prompt


def test_executor_agent_renders_feedback_in_revision_mode(provider, bb):
    bb.plan = "PLAN-TEXT"
    bb.feedback = "FIX LINE 42"
    agent = ExecutorAgent(provider)
    task = AgentMessage(sender="reviewer", receiver="executor", content=bb.issue.to_prompt())

    agent.act(task, bb)

    prompt = provider.calls[0][1]
    assert "FIX LINE 42" in prompt


def test_reviewer_agent_uses_plan_and_patch(provider, bb):
    bb.plan = "PLAN-TEXT"
    bb.patch = "PATCH-TEXT"
    agent = ReviewerAgent(provider)
    task = AgentMessage(sender="executor", receiver="reviewer", content=bb.issue.to_prompt())

    agent.act(task, bb)

    prompt = provider.calls[0][1]
    assert "PLAN-TEXT" in prompt
    assert "PATCH-TEXT" in prompt
    assert "Bug X" in prompt


def test_build_agent_team_returns_four_agents(provider):
    team = build_agent_team(provider)
    assert set(team.keys()) == {"direct", "planner", "executor", "reviewer"}
    for agent in team.values():
        assert isinstance(agent, BaseAgent)
        assert agent.provider is provider


def test_blackboard_log_appends_messages(bb):
    msg = AgentMessage(sender="executor", receiver="reviewer", content="x")
    bb.log(msg)
    assert bb.history == [msg]
