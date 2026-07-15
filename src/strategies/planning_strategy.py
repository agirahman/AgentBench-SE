from models.issue import Issue
from models.patch import Patch
from models.result import ExperimentResult
from models.inference import InferenceRun
from utils.prompt_loader import load_prompt


class PlanningStrategy:

    def __init__(self, provider):
        self.provider = provider

    def run(self, issue: Issue) -> tuple[Patch, ExperimentResult]:
        inferences = []

        # Step 1: Planner
        plan_prompt = load_prompt("planner.md").replace("{{issue}}", issue.to_prompt())
        plan_inf = self.provider.generate(plan_prompt, role="planner")
        inferences.append(plan_inf)

        # Step 2: Executor
        exec_prompt = (
            load_prompt("executor.md")
            .replace("{{issue}}", issue.to_prompt())
            .replace("{{plan}}", plan_inf.response)
        )
        exec_inf = self.provider.generate(exec_prompt, role="executor")
        inferences.append(exec_inf)

        run = InferenceRun(patch=exec_inf.response, inferences=inferences)
        result = ExperimentResult(
            instance_id=issue.instance_id,
            strategy="planning",
            model=self.provider.model,
            run=run,
        )
        return Patch(response=exec_inf.response), result
