from dataclasses import dataclass, field

from models.issue import Issue
from agents.messages import AgentMessage


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
