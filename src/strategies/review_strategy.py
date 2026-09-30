import re

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


# The reviewer is asked to end with a JSON object, but it does not always comply:
# it may wrap the JSON in prose ("The patch is verified correct...\n\n{...}"), or
# emit slightly malformed JSON (a missing comma deep inside a 600-char field).
# Whole-string json.loads() therefore fails on responses that DO carry a verdict,
# and the old 50-character fallback then misread them.
_VERDICT_RE = re.compile(r'"verdict"\s*:\s*"([A-Z_]+)"')

# A rejection phrased as a negated approval ("This is not APPROVED because...").
# The keyword fallback must not read that as consent. Only a negation directly in
# front of the word counts: "no issues, so APPROVED" is still an approval, while
# "cannot be APPROVED" is not.
_NEGATED_APPROVAL_RE = re.compile(
    r"\b(?:not|never|isn'?t|aren'?t|wasn'?t|weren'?t|cannot|can'?t)\s+"
    r"(?:\w+\s+){0,2}APPROVED\b",
    re.I,
)


def _extract_verdict(feedback: str) -> str:
    """Read the reviewer's verdict, tolerating prose framing and malformed JSON.

    Measured failure modes in the recorded experiments:

    * prose THEN json (EXP-20260927-005, EXP-20260929-022 django-11001/review).
      ``json.loads`` fails on the whole string, the first 50 characters are prose,
      so the old fallback read NEEDS_REVISION while the reviewer had said
      APPROVED -- causing a needless revision round.
    * malformed JSON (EXP-20260928-001, django-10924/review: "Expecting ','
      delimiter: line 2 column 613"). ``json.loads`` fails, but the verdict field
      itself is intact and parseable by regex.
    * prose containing the word "APPROVED" before a rejection. The old fallback
      tested ``"APPROVED" in feedback.upper()[:50]``, so a rejection phrased
      "This is not APPROVED because..." read as an approval, and an unreviewed
      patch would ship. That direction is the dangerous one.

    Strategy: take the LAST verdict literal in the text (the final message is the
    authoritative one), which is insensitive to prose, framing and unrelated JSON
    breakage. Only when no verdict literal exists at all do we fall back to the
    keyword scan, and then the default is NEEDS_REVISION -- refusing a patch is
    safe, shipping an unreviewed one is not.
    """
    if not feedback:
        return "NEEDS_REVISION"
    matches = _VERDICT_RE.findall(feedback)
    if matches:
        return matches[-1]
    # No verdict literal. A keyword scan is all that is left, and it must not
    # approve a rejection that merely mentions the word.
    if _NEGATED_APPROVAL_RE.search(feedback):
        return "NEEDS_REVISION"
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

        # Three base acts share the strategy-wide pool (40 -> 13 + 13 + 14), so
        # the base flow costs exactly what direct's and planning's do. A revision
        # act draws from a reserve CARVED OUT of that same pool (Config.REVISION_TOOL_TURNS),
        # not added on top of it: review's whole task still costs 40 turns, the
        # same as direct and planning, so a better review result cannot be
        # explained by a larger budget.
        #
        # with_revisions=True is what carves the reserve out. Passing it for
        # direct or planning would simply cost them 8 turns for an act they never
        # run.
        budget = ToolTurnBudget.from_config(with_revisions=True)

        plan_task = AgentMessage(sender="orchestrator", receiver="planner", kind="task", content=issue.to_agent_prompt(), bb_ops=["get_issue"])
        bb.log(plan_task)
        plan_resp = self.team["planner"].act(
            plan_task,
            bb,
            max_tool_turns=budget.share(3),
            max_cost_usd=budget.cost_share(3),
        )
        budget.spend(getattr(plan_resp.inference, "api_turns", 1))
        budget.spend_cost(plan_resp.inference.cost_usd)
        bb.plan = plan_resp.inference.response
        inferences.append(plan_resp.inference)
        bb.log(AgentMessage(sender="orchestrator", receiver="planner", kind="task", content="", bb_ops=["save_plan"]))

        exec_task = AgentMessage(sender="orchestrator", receiver="executor", kind="task", content=issue.to_agent_prompt(), bb_ops=["get_plan"])
        bb.log(exec_task)
        initial_resp = self.team["executor"].act(
            exec_task,
            bb,
            max_tool_turns=budget.share(2),
            max_cost_usd=budget.cost_share(2),
        )
        budget.spend(getattr(initial_resp.inference, "api_turns", 1))
        budget.spend_cost(initial_resp.inference.cost_usd)
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
        # In per_task mode the reviewer may draw whatever the executor left (minus
        # its own floor), instead of being capped at an even share.
        review_resp = self.team["reviewer"].act(
            review_task,
            bb,
            max_tool_turns=budget.share(1),
            max_cost_usd=budget.cost_share(1),
        )
        budget.spend(getattr(review_resp.inference, "api_turns", 1))
        budget.spend_cost(review_resp.inference.cost_usd)
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
                # A revision is an extra act, but it does not get a fresh base
                # pool: it draws the revision reserve. The split must account for
                # EVERY act still to come, including later rounds — funding only
                # this round would front-load the reserve and leave rounds 2..N on
                # the floor of 1 turn, the same starvation this reserve fixes.
                # Each round is 2 acts (revision + the re-review that must follow
                # it), so the acts remaining are 2 per round, minus the re-review
                # already counted for this round (the revision runs first).
                rounds_left = max(1, Config.MAX_REVISION_TURNS - bb.revision)
                revision_resp = self.team["executor"].act(
                    revision_task,
                    bb,
                    max_tool_turns=budget.share_revision(2 * rounds_left),
                    max_cost_usd=budget.cost_share(2 * rounds_left),
                )
                budget.spend_revision(getattr(revision_resp.inference, "api_turns", 1))
                budget.spend_cost(revision_resp.inference.cost_usd)
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
                # The re-review counts as one of the remaining revision acts.
                re_review_resp = self.team["reviewer"].act(
                    re_review_task,
                    bb,
                    max_tool_turns=budget.share_revision(max(1, 2 * rounds_left - 1)),
                    max_cost_usd=budget.cost_share(max(1, 2 * rounds_left - 1)),
                )
                budget.spend_revision(getattr(re_review_resp.inference, "api_turns", 1))
                budget.spend_cost(re_review_resp.inference.cost_usd)
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
