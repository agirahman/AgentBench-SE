"""Messages exchanged between agents on the blackboard.

``AgentMessage`` doubles as the RUN TRAJECTORY. Every task handed to an agent and
every result it returns is appended to ``Blackboard.history``, and the runner
serialises that history to ``messages.jsonl`` -- so the file is the complete,
ordered record of the run from the orchestrator's first dispatch to the final
patch, not a summary of it.

Two fields make that record complete rather than merely present:

* ``tool_calls`` on a result carries every executed call with its arguments AND
  its observed output, so the ACTIONS are recoverable.
* ``trajectory`` carries the turn-by-turn detail of a tool-calling act: each
  assistant turn with its text and its REASONING channel, then each tool result
  in order. ``tool_calls`` alone is a flat list, which loses which turn produced
  which call and discards the model's reasoning between calls -- so a reader can
  see what the agent did but not why.

Both are populated for tool-using agents. A non-tool agent has nothing to loop
over, so its ``trajectory`` stays empty and the record is its single response.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class AgentMessage:
    sender: str
    receiver: str
    content: str
    kind: str = "task"
    bb_ops: list[str] = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    timestamp: str = ""
    #: Turn-by-turn record of a tool-calling act (empty for a single-shot agent).
    #: Entry shapes are documented in providers/tool_loop.py.
    trajectory: list = field(default_factory=list)
    #: True when the act was cut off by a bound (turns/cost/wall clock) rather than
    #: finishing on its own. Kept on the message because the trajectory is where a
    #: reader looks to answer "did this agent finish, or was it stopped?" -- and a
    #: record that cannot answer that is what made EXP-003's headline unreadable.
    truncated: bool = False

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
            "tool_calls": self.tool_calls,
            "trajectory": self.trajectory,
            "truncated": self.truncated,
            "timestamp": self.timestamp,
        }
