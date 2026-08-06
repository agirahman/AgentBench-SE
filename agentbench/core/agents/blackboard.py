from dataclasses import dataclass, field

from agentbench.core.models.issue import Issue
from agentbench.core.agents.messages import AgentMessage


@dataclass
class Blackboard:
    issue: Issue
    plan: str = ""
    patch: str = ""
    feedback: str = ""
    revision: int = 0
    history: list[AgentMessage] = field(default_factory=list)

    def log(self, message: AgentMessage) -> None:
        self.history.append(message)
