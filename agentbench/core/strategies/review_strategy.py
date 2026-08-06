import json

from agentbench.core.models.issue import Issue
from agentbench.core.models.patch import Patch
from agentbench.core.models.result import ExperimentResult, ExecutionResult, EvaluationResult
from agentbench.core.models.inference import InferenceRun
from agentbench.core.agents.messages import AgentMessage
from agentbench.core.agents.blackboard import Blackboard
from agentbench.core.agents.registry import build_agent_team
from agentbench.core.evaluation.cost import CostCalculator


def _extract_verdict(feedback: str) -> str:
    if not feedback:
        return "NEEDS_REVISION"
    try:
        data = json.loads(feedback)
        return data.get("verdict", "NEEDS_REVISION")
    except json.JSONDecodeError:
        pass
    return "APPROVED" if "APPROVED" in feedback.upper()[:50] else "NEEDS_REVISION"


class ReviewStrategy:

    def __init__(self, provider):
        self.provider = provider
        self.team = build_agent_team(provider)
        self.calculator = CostCalculator()

    def run(self, issue: Issue) -> tuple[Patch, ExperimentResult]:
        bb = Blackboard(issue=issue)
        inferences = []

        plan_task = AgentMessage(sender="orchestrator", receiver="planner", content=issue.to_prompt())
        plan_resp = self.team["planner"].act(plan_task, bb)
        bb.plan = plan_resp.inference.response
        inferences.append(plan_resp.inference)

        exec_task = AgentMessage(sender="planner", receiver="executor", content=issue.to_prompt())
        initial_resp = self.team["executor"].act(exec_task, bb)
        bb.patch = initial_resp.inference.response
        inferences.append(initial_resp.inference)

        review_task = AgentMessage(sender="executor", receiver="reviewer", content=issue.to_prompt())
        review_resp = self.team["reviewer"].act(review_task, bb)
        inferences.append(review_resp.inference)

        needs_revision = _extract_verdict(review_resp.inference.response) != "APPROVED"
        final_response = initial_resp.inference.response
        if needs_revision:
            bb.feedback = review_resp.inference.response
            revision_task = AgentMessage(sender="reviewer", receiver="executor", content=issue.to_prompt())
            revision_resp = self.team["executor"].act(revision_task, bb)
            inferences.append(revision_resp.inference)
            final_response = revision_resp.inference.response
            bb.patch = final_response

        run = InferenceRun(
            patch=final_response,
            inferences=inferences,
            messages=list(bb.history),
        )
        cost = self.calculator.aggregate(inferences)
        exec_res = ExecutionResult(run=run)
        eval_res = EvaluationResult(success=final_response.strip() != "", error="")
        result = ExperimentResult(
            instance_id=issue.instance_id,
            strategy="review",
            model=self.provider.model,
            execution=exec_res,
            cost=cost,
            evaluation=eval_res,
        )
        return Patch(response=final_response), result
