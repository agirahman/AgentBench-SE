import json

from config import Config
from models.issue import Issue
from models.patch import Patch
from models.result import ExperimentResult, ExecutionResult, EvaluationResult
from models.inference import InferenceRun
from agents.messages import AgentMessage
from agents.blackboard import Blackboard
from agents.registry import build_agent_team
from evaluation.cost import CostCalculator


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

    strategy_name = "review"

    def __init__(self, provider):
        self.provider = provider
        self.team = build_agent_team(provider)
        self.calculator = CostCalculator()

    def run(self, issue: Issue) -> tuple[Patch, ExperimentResult]:
        if hasattr(self.provider, "user_id"):
            self.provider.user_id = f"{self.strategy_name}_{issue.instance_id.replace('__', '-')}"
        bb = Blackboard(issue=issue)
        inferences = []

        plan_task = AgentMessage(sender="orchestrator", receiver="planner", kind="task", content=issue.to_agent_prompt(), bb_ops=["get_issue"])
        bb.log(plan_task)
        plan_resp = self.team["planner"].act(plan_task, bb)
        bb.plan = plan_resp.inference.response
        inferences.append(plan_resp.inference)
        bb.log(AgentMessage(sender="orchestrator", receiver="planner", kind="task", content="", bb_ops=["save_plan"]))

        exec_task = AgentMessage(sender="orchestrator", receiver="executor", kind="task", content=issue.to_agent_prompt(), bb_ops=["get_plan"])
        bb.log(exec_task)
        initial_resp = self.team["executor"].act(exec_task, bb)
        bb.patch = initial_resp.inference.response
        inferences.append(initial_resp.inference)
        bb.log(AgentMessage(sender="orchestrator", receiver="executor", kind="task", content="", bb_ops=["save_patch"]))

        review_task = AgentMessage(sender="orchestrator", receiver="reviewer", kind="task", content=issue.to_agent_prompt(), bb_ops=["get_plan", "get_patch"])
        bb.log(review_task)
        review_resp = self.team["reviewer"].act(review_task, bb)
        inferences.append(review_resp.inference)

        needs_revision = _extract_verdict(review_resp.inference.response) != "APPROVED"
        final_response = initial_resp.inference.response
        initial_patch = initial_resp.inference.response.strip()
        initial_truncated = initial_resp.inference.finish_reason == "length"
        if needs_revision and initial_patch and not initial_truncated:
            bb.feedback = review_resp.inference.response
            bb.log(AgentMessage(sender="orchestrator", receiver="reviewer", kind="task", content="", bb_ops=["save_feedback"]))
            while needs_revision and bb.revision < Config.MAX_REVISION_TURNS:
                revision_task = AgentMessage(sender="orchestrator", receiver="executor", kind="task", content=issue.to_agent_prompt(), bb_ops=["get_feedback"])
                bb.log(revision_task)
                revision_resp = self.team["executor"].act(revision_task, bb)
                inferences.append(revision_resp.inference)
                final_response = revision_resp.inference.response
                bb.patch = final_response
                bb.revision += 1
                bb.log(AgentMessage(sender="orchestrator", receiver="executor", kind="task", content="", bb_ops=["save_patch"]))

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
