from models.issue import Issue
from models.patch import Patch
from models.result import ExperimentResult, ExecutionResult, EvaluationResult
from models.inference import InferenceRun
from agents.budget import ToolTurnBudget
from agents.messages import AgentMessage
from agents.blackboard import Blackboard
from agents.registry import build_agent_team
from agents.tools import ensure_repo_root, reset_working_tree, finalize_patch
from evaluation.cost import CostCalculator


class PlanningStrategy:

    strategy_name = "planning"

    def __init__(self, provider):
        self.provider = provider
        self.team = build_agent_team(provider)
        self.calculator = CostCalculator()

    def run(self, issue: Issue) -> tuple[Patch, ExperimentResult]:
        if hasattr(self.provider, "user_id"):
            self.provider.user_id = f"{self.strategy_name}_{issue.instance_id.replace('__', '-')}"
        # Point tool-calling agents at this instance's checked-out repo.
        repo_root = ensure_repo_root(issue.repo, issue.base_commit)
        # Pristine checkout: strategies share one repo per issue, so a previous
        # strategy's edits would otherwise leak into this captured diff.
        # A FAILED reset must stop the run, not be shrugged off: the captured patch
        # is the working-tree diff, so a tree that could not be cleaned contributes
        # its existing content -- and a leftover gold patch would be recorded as
        # this strategy's answer. See direct_strategy.py for the measurement.
        if repo_root is not None and not reset_working_tree(repo_root):
            raise RuntimeError(
                f"checkout for {issue.instance_id} could not be reset to a pristine "
                f"state; refusing to run because the captured diff would include "
                f"pre-existing changes. Run tools/clean_repos.py, then re-run."
            )
        for agent in self.team.values():
            agent.repo_root = str(repo_root) if repo_root else None
        bb = Blackboard(issue=issue)
        inferences = []

        # Two acts share the strategy-wide pool: 60 -> 30 + 30.
        budget = ToolTurnBudget.from_config()

        plan_task = AgentMessage(sender="orchestrator", receiver="planner", kind="task", content=issue.to_agent_prompt(), bb_ops=["get_issue"])
        bb.log(plan_task)
        plan_resp = self.team["planner"].act(
            plan_task,
            bb,
            max_tool_turns=budget.share(2),
            max_cost_usd=budget.cost_share(2),
        )
        budget.spend(getattr(plan_resp.inference, "api_turns", 1))
        budget.spend_cost(plan_resp.inference.cost_usd)
        bb.plan = plan_resp.inference.response
        inferences.append(plan_resp.inference)
        bb.log(AgentMessage(sender="orchestrator", receiver="planner", kind="task", content="", bb_ops=["save_plan"]))

        exec_task = AgentMessage(sender="orchestrator", receiver="executor", kind="task", content=issue.to_agent_prompt(), bb_ops=["get_plan"])
        bb.log(exec_task)
        exec_resp = self.team["executor"].act(
            exec_task,
            bb,
            max_tool_turns=budget.share(1),
            max_cost_usd=budget.cost_share(1),
        )
        budget.spend_cost(exec_resp.inference.cost_usd)
        bb.patch = exec_resp.inference.response
        inferences.append(exec_resp.inference)
        bb.log(AgentMessage(sender="orchestrator", receiver="executor", kind="task", content="", bb_ops=["save_patch"]))

        # Executor is the role that edits; take the patch from the repo, not text.
        patch_text = finalize_patch(repo_root, exec_resp.inference.response)

        run = InferenceRun(
            patch=patch_text,
            inferences=inferences,
            messages=list(bb.history),
        )
        cost = self.calculator.aggregate(inferences)
        exec_res = ExecutionResult(run=run)
        eval_res = EvaluationResult(success=patch_text.strip() != "", error="")
        result = ExperimentResult(
            instance_id=issue.instance_id,
            strategy="planning",
            model=self.provider.model,
            execution=exec_res,
            cost=cost,
            evaluation=eval_res,
        )
        return Patch(response=patch_text), result
