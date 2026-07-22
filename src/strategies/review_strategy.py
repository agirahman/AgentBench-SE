import json

from models.issue import Issue
from models.patch import Patch
from models.result import ExperimentResult, ExecutionResult, EvaluationResult
from models.inference import InferenceRun
from utils.prompt_loader import load_prompt_or_default
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

    def __init__(self, provider):
        self.provider = provider
        self.calculator = CostCalculator()

    def run(self, issue: Issue) -> tuple[Patch, ExperimentResult]:
        inferences = []

        plan_prompt = load_prompt_or_default("planner.md", "{{issue}}\n").replace("{{issue}}", issue.to_prompt())
        plan_inf = self.provider.generate(plan_prompt, role="planner")
        inferences.append(plan_inf)

        exec_prompt = (
            load_prompt_or_default("executor.md", "{{issue}}\n\nPlan:\n{{plan}}")
            .replace("{{issue}}", issue.to_prompt())
            .replace("{{plan}}", plan_inf.response)
        )
        initial_inf = self.provider.generate(exec_prompt, role="executor")
        inferences.append(initial_inf)

        review_prompt = (
            load_prompt_or_default("reviewer.md", "{{issue}}\n\nPlan:\n{{plan}}\n\nPatch:\n{{patch}}")
            .replace("{{issue}}", issue.to_prompt())
            .replace("{{plan}}", plan_inf.response)
            .replace("{{patch}}", initial_inf.response)
        )
        review_inf = self.provider.generate(review_prompt, role="reviewer")
        inferences.append(review_inf)

        needs_revision = _extract_verdict(review_inf.response) != "APPROVED"
        final_response = initial_inf.response
        if needs_revision:
            revision_prompt = (
                load_prompt_or_default("executor.md", "{{issue}}\n\nPlan:\n{{plan}}\n\nFeedback:\n{{feedback}}")
                .replace("{{issue}}", issue.to_prompt())
                .replace("{{plan}}", plan_inf.response)
                .replace("{{feedback}}", review_inf.response)
            )
            revision_inf = self.provider.generate(revision_prompt, role="executor")
            inferences.append(revision_inf)
            final_response = revision_inf.response

        run = InferenceRun(patch=final_response, inferences=inferences)
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
