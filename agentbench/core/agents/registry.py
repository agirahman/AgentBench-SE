from typing import TypeAlias

from agentbench.core.agents.base import BaseAgent
from agentbench.core.agents.direct_agent import DirectAgent
from agentbench.core.agents.planner_agent import PlannerAgent
from agentbench.core.agents.executor_agent import ExecutorAgent
from agentbench.core.agents.reviewer_agent import ReviewerAgent

AgentInstance: TypeAlias = BaseAgent


def build_agent_team(provider) -> dict[str, AgentInstance]:
    return {
        "direct": DirectAgent(provider),
        "planner": PlannerAgent(provider),
        "executor": ExecutorAgent(provider),
        "reviewer": ReviewerAgent(provider),
    }
