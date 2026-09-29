from models.issue import Issue
from models.patch import Patch
from models.result import ExperimentResult, ExecutionResult, CostSummary, EvaluationResult
from models.inference import InferenceRun
from agents.budget import ToolTurnBudget
from agents.messages import AgentMessage
from agents.blackboard import Blackboard
from agents.registry import build_agent_team
from agents.tools import ensure_repo_root, reset_working_tree, finalize_patch
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
        # Start from a pristine checkout: strategies share one repo per issue, so
        # without this the captured diff would include the previous strategy's
        # edits as well as this one's.
        reset_working_tree(repo_root)
        for agent in self.team.values():
            agent.repo_root = str(repo_root) if repo_root else None
        bb = Blackboard(issue=issue)
        # One act, so it gets the whole strategy-wide pool: direct is not
        # penalised for having a single agent (see agents/budget.py).
        budget = ToolTurnBudget.from_config()
        task = AgentMessage(sender="orchestrator", receiver="direct", kind="task", content=issue.to_agent_prompt(), bb_ops=["get_issue"])
        bb.log(task)
        resp = self.team["direct"].act(
            task,
            bb,
            max_tool_turns=budget.share(1),
            max_cost_usd=budget.cost_share(1),
        )

        # Under tool calling the agent edits files, so the authoritative patch is
        # the working-tree diff; otherwise fall back to the model's own text.
        patch_text = finalize_patch(repo_root, resp.inference.response)

        run = InferenceRun(
            patch=patch_text,
            inferences=[resp.inference],
            messages=list(bb.history),
        )
        cost = self.calculator.aggregate([resp.inference])
        exec_res = ExecutionResult(run=run)
        eval_res = EvaluationResult(success=patch_text.strip() != "", error="")
        result = ExperimentResult(
            instance_id=issue.instance_id,
            strategy="direct",
            model=self.provider.model,
            execution=exec_res,
            cost=cost,
            evaluation=eval_res,
        )
        return Patch(response=patch_text), result
