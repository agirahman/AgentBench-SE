from models.issue import Issue
from models.patch import Patch
from models.result import ExperimentResult, ExecutionResult, CostSummary, EvaluationResult
from models.inference import InferenceRun
from agents.messages import AgentMessage
from agents.blackboard import Blackboard
from agents.registry import build_agent_team
from agents.tools import ensure_repo_root
from evaluation.cost import CostCalculator


class DirectStrategy:

    strategy_name = "direct"

    def __init__(self, provider):
        self.provider = provider
        self.team = build_agent_team(provider)
        self.calculator = CostCalculator()

    def run(self, issue: Issue) -> tuple[Patch, ExperimentResult]:
        if hasattr(self.provider, "user_id"):
            self.provider.user_id = f"{self.strategy_name}_{issue.instance_id.replace('__', '-')}"
        repo_root = ensure_repo_root(issue.repo, issue.base_commit)
        for agent in self.team.values():
            agent.repo_root = str(repo_root) if repo_root else None
        bb = Blackboard(issue=issue)
        task = AgentMessage(sender="orchestrator", receiver="direct", kind="task", content=issue.to_agent_prompt(), bb_ops=["get_issue"])
        bb.log(task)
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
