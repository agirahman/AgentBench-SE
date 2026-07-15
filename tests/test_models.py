import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models.inference import InferenceResult, InferenceRun
from models.result import ExecutionResult, CostSummary, EvaluationResult, ExperimentResult
from evaluation.cost import CostCalculator, PricingTable


def _make_inference(role="executor", prompt=500, completion=200, model="gemini-3-flash-preview"):
    return InferenceResult(
        role=role,
        response="patch content",
        usage={
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
        model=model,
    )


class TestInferenceRun:
    def test_total_tokens(self):
        i1 = _make_inference(prompt=100, completion=50)
        i2 = _make_inference(prompt=200, completion=100)
        run = InferenceRun(patch="patch", inferences=[i1, i2])
        assert run.total_tokens == 450

    def test_total_time(self):
        i1 = _make_inference()
        i1.execution_time = 1.0
        i2 = _make_inference()
        i2.execution_time = 2.0
        run = InferenceRun(patch="patch", inferences=[i1, i2])
        assert run.total_time == pytest.approx(3.0)


class TestExecutionResult:
    def test_delegates_to_run(self):
        i1 = _make_inference(prompt=1000, completion=500)
        i1.execution_time = 3.5
        run = InferenceRun(patch="patch text", inferences=[i1])
        exec_res = ExecutionResult(run=run)
        assert exec_res.inference_count == 1
        assert exec_res.execution_time == pytest.approx(3.5)
        assert exec_res.prompt_tokens == 1000
        assert exec_res.completion_tokens == 500
        assert exec_res.total_tokens == 1500
        assert exec_res.patch == "patch text"
        assert exec_res.patch_preview == "patch text"


class TestCostSummary:
    def test_aggregate(self):
        calc = CostCalculator()
        i1 = _make_inference(prompt=1000, completion=500)
        i2 = _make_inference(prompt=200, completion=100)
        cost = calc.aggregate([i1, i2])
        assert cost.total_cost_usd == pytest.approx(
            (1000/1e6 * 0.25) + (500/1e6 * 1.50) + (200/1e6 * 0.25) + (100/1e6 * 1.50)
        )
        assert cost.input_cost_usd == pytest.approx((1000 + 200) / 1e6 * 0.25)
        assert cost.output_cost_usd == pytest.approx((500 + 100) / 1e6 * 1.50)
        assert cost.pricing_version == "2026-07"


class TestExperimentResultStructure:
    def test_nested_fields_only(self):
        i = _make_inference()
        i.execution_time = 1.0
        run = InferenceRun(patch="patch", inferences=[i])
        calc = CostCalculator()
        cost = calc.aggregate([i])
        exec_res = ExecutionResult(run=run)
        eval_res = EvaluationResult(success=True, error="")
        result = ExperimentResult(
            instance_id="django__django-100",
            strategy="direct",
            model="gemini-3-flash-preview",
            execution=exec_res,
            cost=cost,
            evaluation=eval_res,
        )
        assert result.execution.inference_count == 1
        assert result.cost.total_cost_idr > 0
        assert result.evaluation.success is True
        assert result.instance_id == "django__django-100"
