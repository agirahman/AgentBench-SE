import json

from config import Config
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
        repo_root = ensure_repo_root(issue.repo, issue.base_commit)
        # Pristine checkout: strategies share one repo per issue, so a previous
        # strategy's edits would otherwise leak into this captured diff.
        reset_working_tree(repo_root)
        for agent in self.team.values():
            agent.repo_root = str(repo_root) if repo_root else None
        bb = Blackboard(issue=issue)
        inferences = []

        # Three base acts share the strategy-wide pool: 60 -> 20 + 20 + 20.
        # A revision act draws from whatever the earlier acts left unused, so
        # the total stays bounded however many revisions run.
        budget = ToolTurnBudget.from_config()

        plan_task = AgentMessage(sender="orchestrator", receiver="planner", kind="task", content=issue.to_agent_prompt(), bb_ops=["get_issue"])
        bb.log(plan_task)
        plan_resp = self.team["planner"].act(plan_task, bb, max_tool_turns=budget.share(3))
        budget.spend(getattr(plan_resp.inference, "api_turns", 1))
        bb.plan = plan_resp.inference.response
        inferences.append(plan_resp.inference)
        bb.log(AgentMessage(sender="orchestrator", receiver="planner", kind="task", content="", bb_ops=["save_plan"]))

        exec_task = AgentMessage(sender="orchestrator", receiver="executor", kind="task", content=issue.to_agent_prompt(), bb_ops=["get_plan"])
        bb.log(exec_task)
        initial_resp = self.team["executor"].act(exec_task, bb, max_tool_turns=budget.share(2))
        budget.spend(getattr(initial_resp.inference, "api_turns", 1))
        inferences.append(initial_resp.inference)

        # The reviewer must inspect the REAL change, so hand it the diff captured
        # from the working tree rather than the executor's prose summary. This
        # makes the review meaningful: it critiques an applyable diff instead of
        # re-reading text that may not match the repository at all.
        initial_diff = finalize_patch(repo_root, initial_resp.inference.response)
        bb.patch = initial_diff
        bb.log(AgentMessage(sender="orchestrator", receiver="executor", kind="task", content="", bb_ops=["save_patch"]))

        review_task = AgentMessage(sender="orchestrator", receiver="reviewer", kind="task", content=issue.to_agent_prompt(), bb_ops=["get_plan", "get_patch"])
        bb.log(review_task)
        review_resp = self.team["reviewer"].act(review_task, bb, max_tool_turns=budget.share(1))
        budget.spend(getattr(review_resp.inference, "api_turns", 1))
        inferences.append(review_resp.inference)

        # Candidate patches, each captured at the moment it was produced. Shipping
        # `finalize_patch()` at the very end instead returns whatever the working
        # tree holds LAST — which is how a rejected revision silently replaced a
        # working patch (EXP-20260927-007, django-10924 review: the unverified
        # revision was the one evaluated, and it failed, while planning's single
        # patch on the same issue resolved).
        # NOTE: never `.strip()` a diff. A hunk's last line may legitimately be a
        # whitespace-only context line (" "), and stripping removes it, leaving a
        # body one line shorter than its @@ header declares. The result is a
        # corrupt patch (git: "corrupt patch at line N") that patch_status then
        # mislabels NORMALIZE. Keep the diff byte-exact; strip only for emptiness.
        initial_patch = initial_diff
        approved = _extract_verdict(review_resp.inference.response) == "APPROVED"
        candidates: list[tuple[str, bool]] = [(initial_patch, approved)]

        initial_truncated = initial_resp.inference.finish_reason == "length"
        if not approved and initial_patch.strip() and not initial_truncated:
            bb.feedback = review_resp.inference.response
            bb.log(AgentMessage(sender="orchestrator", receiver="reviewer", kind="task", content="", bb_ops=["save_feedback"]))
            while not approved and bb.revision < Config.MAX_REVISION_TURNS:
                revision_task = AgentMessage(sender="orchestrator", receiver="executor", kind="task", content=issue.to_agent_prompt(), bb_ops=["get_feedback"])
                bb.log(revision_task)
                # A revision is an extra act, but it does not get a fresh pool:
                # it draws the remainder, split across itself and the re-review
                # that must follow it. When the base acts used their whole
                # share this grants the floor of 1 turn rather than nothing.
                revision_resp = self.team["executor"].act(
                    revision_task, bb, max_tool_turns=budget.share(2)
                )
                budget.spend(getattr(revision_resp.inference, "api_turns", 1))
                inferences.append(revision_resp.inference)
                # Capture this revision's own diff now, while it is still the
                # working-tree state (diffs are cumulative against HEAD).
                bb.patch = finalize_patch(repo_root, revision_resp.inference.response)
                bb.revision += 1
                bb.log(AgentMessage(sender="orchestrator", receiver="executor", kind="task", content="", bb_ops=["save_patch"]))

                revised_patch = bb.patch
                if not revised_patch.strip():
                    break

                # Re-review the revision. A revision nobody checked is not an
                # improvement, it is an unverified rewrite — and it must not be
                # shipped merely because it came later.
                re_review_task = AgentMessage(sender="orchestrator", receiver="reviewer", kind="task", content=issue.to_agent_prompt(), bb_ops=["get_plan", "get_patch"])
                bb.log(re_review_task)
                re_review_resp = self.team["reviewer"].act(
                    re_review_task, bb, max_tool_turns=budget.share(1)
                )
                budget.spend(getattr(re_review_resp.inference, "api_turns", 1))
                inferences.append(re_review_resp.inference)
                approved = _extract_verdict(re_review_resp.inference.response) == "APPROVED"
                candidates.append((revised_patch, approved))
                if approved:
                    break
                bb.feedback = re_review_resp.inference.response
                bb.log(AgentMessage(sender="orchestrator", receiver="reviewer", kind="task", content="", bb_ops=["save_feedback"]))

        # Prefer a patch the reviewer actually approved. If none was approved,
        # ship the executor's first attempt: every candidate was rejected, so a
        # later rewrite carries no evidence of being better, and there is direct
        # evidence in EXP-20260927-007 of one being worse.
        patch_text = next(
            (p for p, ok in candidates if ok and p),
            initial_patch or candidates[-1][0],
        )

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
            strategy="review",
            model=self.provider.model,
            execution=exec_res,
            cost=cost,
            evaluation=eval_res,
        )
        return Patch(response=patch_text), result
