from agents.base import BaseAgent
from agents.messages import AgentMessage
from agents.blackboard import Blackboard


class ExecutorAgent(BaseAgent):
    name = "executor"
    prompt_file = "executor.md"
    default_template = "{{issue}}\n\nPlan:\n{{plan}}"

    def _render(self, task: AgentMessage, context: Blackboard) -> str:
        context.bb_ops.append("get_issue")
        if context.plan:
            context.bb_ops.append("get_plan")
        prompt = self.template.replace("{{issue}}", task.content)
        prompt = prompt.replace("{{plan}}", context.plan)
        if context.feedback:
            context.bb_ops.append("get_feedback")
            if "{{feedback}}" in self.template:
                prompt = prompt.replace("{{feedback}}", context.feedback)
            else:
                prompt = prompt + f"\n\nReviewer Feedback:\n{context.feedback}"
        return prompt
