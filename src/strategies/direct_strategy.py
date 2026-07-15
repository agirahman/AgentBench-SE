from models.issue import Issue
from models.patch import Patch
from models.result import ExperimentResult
from models.inference import InferenceRun
from utils.prompt_loader import load_prompt


class DirectStrategy:

    def __init__(self, provider):
        self.provider = provider

    def run(self, issue: Issue) -> tuple[Patch, ExperimentResult]:
        prompt = load_prompt("direct_prompt.md").replace("{{issue}}", issue.to_prompt())
        inf = self.provider.generate(prompt, role="executor")

        run = InferenceRun(patch=inf.response, inferences=[inf])
        result = ExperimentResult(
            instance_id=issue.instance_id,
            strategy="direct",
            model=self.provider.model,
            run=run,
        )
        return Patch(response=inf.response), result
