from agents.base import BaseAgent
from agents.messages import AgentMessage
from agents.blackboard import Blackboard


class ReviewerAgent(BaseAgent):
    name = "reviewer"
    prompt_file = "reviewer.md"
    default_template = "{{issue}}\n\nPlan:\n{{plan}}\n\nPatch:\n{{patch}}"

    def _render(self, task: AgentMessage, context: Blackboard) -> str:
        context.bb_ops.append("get_issue")
        if context.plan:
            context.bb_ops.append("get_plan")
        if context.patch:
            context.bb_ops.append("get_patch")
        prompt = self.template.replace("{{issue}}", task.content)
        prompt = prompt.replace("{{plan}}", context.plan)
        prompt = prompt.replace("{{patch}}", context.patch)
        return prompt
