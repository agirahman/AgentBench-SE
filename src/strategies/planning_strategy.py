from models.issue import Issue
from models.patch import Patch
from models.result import ExperimentResult, ExecutionResult, EvaluationResult
from models.inference import InferenceRun
from agents.messages import AgentMessage
from agents.blackboard import Blackboard
from agents.registry import build_agent_team
from evaluation.cost import CostCalculator


class PlanningStrategy:

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
        exec_resp = self.team["executor"].act(exec_task, bb)
        bb.patch = exec_resp.inference.response
        inferences.append(exec_resp.inference)

        run = InferenceRun(
            patch=exec_resp.inference.response,
            inferences=inferences,
            messages=list(bb.history),
        )
        cost = self.calculator.aggregate(inferences)
        exec_res = ExecutionResult(run=run)
        eval_res = EvaluationResult(success=exec_resp.inference.response.strip() != "", error="")
        result = ExperimentResult(
            instance_id=issue.instance_id,
            strategy="planning",
            model=self.provider.model,
            execution=exec_res,
            cost=cost,
            evaluation=eval_res,
        )
        return Patch(response=exec_resp.inference.response), result
