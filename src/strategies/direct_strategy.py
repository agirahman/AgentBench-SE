from models.issue import Issue
from models.patch import Patch

from utils.prompt_loader import load_prompt


class DirectStrategy:
    """
    Baseline Strategy

    Issue
        ↓
    Prompt
        ↓
    Gemini
        ↓
    Patch
    """

    def __init__(self, provider):
        self.provider = provider

    def run(self, issue: Issue) -> Patch:

        prompt_template = load_prompt("direct_prompt.md")

        prompt = prompt_template.replace(
            "{{issue}}",
            issue.to_prompt()
        )

        response = self.provider.generate(prompt)

        return Patch(content=response)