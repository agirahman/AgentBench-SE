from typing import TypeAlias

from config import Config
from agents.base import BaseAgent
from agents.direct_agent import DirectAgent
from agents.planner_agent import PlannerAgent
from agents.executor_agent import ExecutorAgent
from agents.reviewer_agent import ReviewerAgent

AgentInstance: TypeAlias = BaseAgent


def build_agent_team(provider) -> dict[str, AgentInstance]:
    # Tool calling is only meaningful when enabled AND the provider supports it.
    toolcall_active = Config.TOOLCALL_ENABLED and hasattr(provider, "generate_with_tools")
    team = {
        "direct": DirectAgent(provider),
        "planner": PlannerAgent(provider),
        "executor": ExecutorAgent(provider),
        "reviewer": ReviewerAgent(provider),
    }
    if toolcall_active:
        for agent in team.values():
            agent.use_tools = True
    return team
