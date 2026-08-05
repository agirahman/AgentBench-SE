from agents.base import BaseAgent
from agents.messages import AgentMessage
from agents.blackboard import Blackboard


class PlannerAgent(BaseAgent):
    name = "planner"
    prompt_file = "planner.md"
    default_template = "{{issue}}\n"

    def _render(self, task: AgentMessage, context: Blackboard) -> str:
        return self.template.replace("{{issue}}", task.content)
