from agentbench.core.agents.base import BaseAgent
from agentbench.core.agents.messages import AgentMessage
from agentbench.core.agents.blackboard import Blackboard


class DirectAgent(BaseAgent):
    name = "direct"
    prompt_file = "direct_prompt.md"
    default_template = "{{issue}}\n"

    def _render(self, task: AgentMessage, context: Blackboard) -> str:
        return self.template.replace("{{issue}}", task.content)
