from models.issue import Issue
from models.patch import Patch
from models.result import ExperimentResult, ExecutionResult, EvaluationResult
from models.inference import InferenceRun
from utils.prompt_loader import load_prompt
from evaluation.cost import CostCalculator


class PlanningStrategy:

    def __init__(self, provider):
        self.provider = provider
        self.calculator = CostCalculator()

    def run(self, issue: Issue) -> tuple[Patch, ExperimentResult]:
        inferences = []

        plan_prompt = load_prompt("planner.md").replace("{{issue}}", issue.to_prompt())
        plan_inf = self.provider.generate(plan_prompt, role="planner")
        inferences.append(plan_inf)

        exec_prompt = (
            load_prompt("executor.md")
            .replace("{{issue}}", issue.to_prompt())
            .replace("{{plan}}", plan_inf.response)
        )
        exec_inf = self.provider.generate(exec_prompt, role="executor")
        inferences.append(exec_inf)

        run = InferenceRun(patch=exec_inf.response, inferences=inferences)
        cost = self.calculator.aggregate(inferences)
        exec_res = ExecutionResult(run=run)
        eval_res = EvaluationResult(
            success=exec_inf.response.strip() != "", error=""
        )
        result = ExperimentResult(
            instance_id=issue.instance_id,
            strategy="planning",
            model=self.provider.model,
            execution=exec_res,
            cost=cost,
            evaluation=eval_res,
        )
        return Patch(response=exec_inf.response), result
