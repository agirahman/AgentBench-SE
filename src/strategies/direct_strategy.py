import time

from models.issue import Issue
from models.patch import Patch
from models.result import ExperimentResult
from utils.prompt_loader import load_prompt


class DirectStrategy:

    def __init__(self, provider):
        self.provider = provider

    def run(self, issue: Issue) -> tuple[Patch, ExperimentResult]:
        t0 = time.perf_counter()

        prompt = load_prompt("direct_prompt.md").replace("{{issue}}", issue.to_prompt())
        response = self.provider.generate(prompt)

        elapsed = time.perf_counter() - t0
        usage = self.provider.last_usage or {}

        patch = Patch(response=response)
        result = ExperimentResult(
            instance_id=issue.instance_id,
            strategy="direct",
            execution_time=elapsed,
            inference_count=1,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            patch_preview=response[:100],
        )
        return patch, result