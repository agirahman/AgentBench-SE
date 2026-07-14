import time
from datetime import datetime, timezone

from models.issue import Issue
from models.patch import Patch
from models.result import ExperimentResult
from utils.prompt_loader import load_prompt


class PlanningStrategy:

    def __init__(self, provider):
        self.provider = provider

    def run(self, issue: Issue) -> tuple[Patch, ExperimentResult]:
        inferences = 0
        total_time = 0.0
        total_prompt = 0
        total_completion = 0
        total_tokens = 0

        # Step 1: Planner
        t0 = time.perf_counter()
        plan = self.provider.generate(
            load_prompt("planner.md").replace("{{issue}}", issue.to_prompt())
        )
        elapsed = time.perf_counter() - t0
        inferences += 1
        total_time += elapsed
        u = self.provider.last_usage or {}
        total_prompt += u.get("prompt_tokens", 0)
        total_completion += u.get("completion_tokens", 0)
        total_tokens += u.get("total_tokens", 0)

        # Step 2: Executor
        t0 = time.perf_counter()
        exec_prompt = (
            load_prompt("executor.md")
            .replace("{{issue}}", issue.to_prompt())
            .replace("{{plan}}", plan)
        )
        response = self.provider.generate(exec_prompt)
        elapsed = time.perf_counter() - t0
        inferences += 1
        total_time += elapsed
        u = self.provider.last_usage or {}
        total_prompt += u.get("prompt_tokens", 0)
        total_completion += u.get("completion_tokens", 0)
        total_tokens += u.get("total_tokens", 0)

        patch = Patch(response=response)
        result = ExperimentResult(
            instance_id=issue.instance_id,
            strategy="planning",
            execution_time=total_time,
            inference_count=inferences,
            prompt_tokens=total_prompt,
            completion_tokens=total_completion,
            total_tokens=total_tokens,
            patch_preview=response[:100],
        )
        result.timestamp = datetime.now(timezone.utc).isoformat()
        return patch, result