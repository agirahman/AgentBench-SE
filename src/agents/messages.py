from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class AgentMessage:
    sender: str
    receiver: str
    content: str
    kind: str = "task"
    bb_ops: list[str] = field(default_factory=list)
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
            "bb_ops": self.bb_ops,
            "timestamp": self.timestamp,
        }
