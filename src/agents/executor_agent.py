from agents.base import BaseAgent
from agents.messages import AgentMessage
from agents.blackboard import Blackboard


class ExecutorAgent(BaseAgent):
    name = "executor"
    prompt_file = "executor.md"
    default_template = "{{issue}}\n\nPlan:\n{{plan}}"

    def _render(self, task: AgentMessage, context: Blackboard, template: str) -> str:
        context.bb_ops.append("get_issue")
        if context.plan:
            context.bb_ops.append("get_plan")
        dynamic = template.replace("{{issue}}", task.content)
        dynamic = dynamic.replace("{{plan}}", context.plan)
        if context.feedback:
            context.bb_ops.append("get_feedback")
            if "{{feedback}}" in template:
                dynamic = dynamic.replace("{{feedback}}", context.feedback)
            else:
                dynamic = dynamic + f"\n\nReviewer Feedback:\n{context.feedback}"
        return self._wrap(dynamic)
