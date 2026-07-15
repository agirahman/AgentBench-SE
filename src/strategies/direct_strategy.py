from models.issue import Issue
from models.patch import Patch
from models.result import ExperimentResult, ExecutionResult, CostSummary, EvaluationResult
from models.inference import InferenceRun
from utils.prompt_loader import load_prompt
from evaluation.cost import CostCalculator


class DirectStrategy:

    def __init__(self, provider):
        self.provider = provider
        self.calculator = CostCalculator()

    def run(self, issue: Issue) -> tuple[Patch, ExperimentResult]:
        prompt = load_prompt("direct_prompt.md").replace("{{issue}}", issue.to_prompt())
        inf = self.provider.generate(prompt, role="executor")

        run = InferenceRun(patch=inf.response, inferences=[inf])
        # cost aggregation (single inference)
        cost_res = self.calculator.calculate(inf)
        cost = CostSummary(
            input_cost_usd=cost_res.input_cost_usd,
            output_cost_usd=cost_res.output_cost_usd,
            total_cost_usd=cost_res.total_cost_usd,
            total_cost_idr=cost_res.total_cost_idr,
            pricing_version="2026-07",
        )
        eval_res = EvaluationResult(success=inf.response.strip() != "", error="", timestamp="")
        exec_res = ExecutionResult(run=run)
        result = ExperimentResult(
            instance_id=issue.instance_id,
            strategy="direct",
            model=self.provider.model,
            execution=exec_res,
            cost=cost,
            evaluation=eval_res,
        )
        return Patch(response=inf.response), result

