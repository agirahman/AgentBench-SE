from agentbench.core.agents.base import BaseAgent
from agentbench.core.agents.messages import AgentMessage
from agentbench.core.agents.blackboard import Blackboard


class PlannerAgent(BaseAgent):
    name = "planner"
    prompt_file = "planner.md"
    default_template = "{{issue}}\n"

    def _render(self, task: AgentMessage, context: Blackboard) -> str:
        return self.template.replace("{{issue}}", task.content)
