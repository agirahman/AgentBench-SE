import json

from models.issue import Issue
from models.patch import Patch
from models.result import ExperimentResult
from models.inference import InferenceRun
from utils.prompt_loader import load_prompt


def _extract_verdict(feedback: str) -> str:
    """Coba ambil verdict dari JSON, fallback ke deteksi teks."""
    if not feedback:
        return "NEEDS_REVISION"
    try:
        data = json.loads(feedback)
        verdict = data.get("verdict", "NEEDS_REVISION")
        return verdict
    except json.JSONDecodeError:
        pass
    return "APPROVED" if "APPROVED" in feedback.upper()[:50] else "NEEDS_REVISION"


class ReviewStrategy:

    def __init__(self, provider):
        self.provider = provider

    def run(self, issue: Issue) -> tuple[Patch, ExperimentResult]:
        inferences = []

        # Step 1: Planner
        plan_prompt = load_prompt("planner.md").replace("{{issue}}", issue.to_prompt())
        plan_inf = self.provider.generate(plan_prompt, role="planner")
        inferences.append(plan_inf)

        # Step 2: Executor (initial patch)
        exec_prompt = (
            load_prompt("executor.md")
            .replace("{{issue}}", issue.to_prompt())
            .replace("{{plan}}", plan_inf.response)
        )
        initial_inf = self.provider.generate(exec_prompt, role="executor")
        inferences.append(initial_inf)

        # Step 3: Reviewer
        review_prompt = (
            load_prompt("reviewer.md")
            .replace("{{issue}}", issue.to_prompt())
            .replace("{{plan}}", plan_inf.response)
            .replace("{{patch}}", initial_inf.response)
        )
        review_inf = self.provider.generate(review_prompt, role="reviewer")
        inferences.append(review_inf)

        # Step 4: Revisi jika tidak APPROVED
        needs_revision = _extract_verdict(review_inf.response) != "APPROVED"
        final_response = initial_inf.response
        if needs_revision:
            revision_prompt = (
                load_prompt("executor.md")
                .replace("{{issue}}", issue.to_prompt())
                .replace("{{plan}}", plan_inf.response)
                .replace("{{feedback}}", review_inf.response)
            )
            revision_inf = self.provider.generate(revision_prompt, role="executor")
            inferences.append(revision_inf)
            final_response = revision_inf.response

        run = InferenceRun(patch=final_response, inferences=inferences)
        result = ExperimentResult(
            instance_id=issue.instance_id,
            strategy="review",
            model=self.provider.model,
            run=run,
        )
        return Patch(response=final_response), result
