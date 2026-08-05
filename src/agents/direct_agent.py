from agents.base import BaseAgent
from agents.messages import AgentMessage
from agents.blackboard import Blackboard


class DirectAgent(BaseAgent):
    name = "direct"
    prompt_file = "direct_prompt.md"
    default_template = "{{issue}}\n"

    def _render(self, task: AgentMessage, context: Blackboard) -> str:
        return self.template.replace("{{issue}}", task.content)
