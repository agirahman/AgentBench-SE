from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class AgentMessage:
    sender: str
    receiver: str
    content: str
    kind: str = "task"
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "sender": self.sender,
            "receiver": self.receiver,
            "kind": self.kind,
            "content": self.content,
            "timestamp": self.timestamp,
        }
