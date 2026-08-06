from models.issue import Issue
from models.patch import Patch
from models.result import ExperimentResult, ExecutionResult, CostSummary, EvaluationResult
from models.inference import InferenceRun
from agents.messages import AgentMessage
from agents.blackboard import Blackboard
from agents.registry import build_agent_team
from evaluation.cost import CostCalculator


class DirectStrategy:

    def __init__(self, provider):
        self.provider = provider
        self.team = build_agent_team(provider)
        self.calculator = CostCalculator()

    def run(self, issue: Issue) -> tuple[Patch, ExperimentResult]:
        bb = Blackboard(issue=issue)
        task = AgentMessage(sender="orchestrator", receiver="direct", content=issue.to_prompt())
        resp = self.team["direct"].act(task, bb)

        run = InferenceRun(
            patch=resp.inference.response,
            inferences=[resp.inference],
            messages=list(bb.history),
        )
        cost = self.calculator.aggregate([resp.inference])
        exec_res = ExecutionResult(run=run)
        eval_res = EvaluationResult(success=resp.inference.response.strip() != "", error="")
        result = ExperimentResult(
            instance_id=issue.instance_id,
            strategy="direct",
            model=self.provider.model,
            execution=exec_res,
            cost=cost,
            evaluation=eval_res,
        )
        return Patch(response=resp.inference.response), result
