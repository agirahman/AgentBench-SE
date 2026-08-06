from typing import TypeAlias

from agents.base import BaseAgent
from agents.direct_agent import DirectAgent
from agents.planner_agent import PlannerAgent
from agents.executor_agent import ExecutorAgent
from agents.reviewer_agent import ReviewerAgent

AgentInstance: TypeAlias = BaseAgent


def build_agent_team(provider) -> dict[str, AgentInstance]:
    return {
        "direct": DirectAgent(provider),
        "planner": PlannerAgent(provider),
        "executor": ExecutorAgent(provider),
        "reviewer": ReviewerAgent(provider),
    }
