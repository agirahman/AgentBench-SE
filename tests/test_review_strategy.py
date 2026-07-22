from types import SimpleNamespace

from strategies.review_strategy import ReviewStrategy, _extract_verdict


class DummyInference:
    def __init__(self, response, role):
        self.response = response
        self.role = role
        self.usage = {}
        self.finish_reason = "STOP"
        self.model = "demo-model"
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0


class DummyProvider:
    def __init__(self):
        self.model = "demo-model"
        self.calls = []

    def generate(self, prompt, role=""):
        self.calls.append((role, prompt))
        if role == "planner":
            return DummyInference("plan", role)
        if role == "executor":
            return DummyInference("patch", role)
        return DummyInference('{"verdict": "APPROVED"}', role)


def test_extract_verdict_defaults_to_revision_for_invalid_json():
    assert _extract_verdict("not-json") == "NEEDS_REVISION"


def test_review_strategy_uses_fallback_prompt_when_template_missing(monkeypatch):
    provider = DummyProvider()
    strategy = ReviewStrategy(provider)

    monkeypatch.setattr(
        "strategies.review_strategy.load_prompt_or_default",
        lambda filename, default="": str(default),
    )

    issue = SimpleNamespace(instance_id="ISSUE-1", problem_statement="Bug", to_prompt=lambda: "Issue")
    patch, result = strategy.run(issue)

    assert patch.response == "patch"
    assert result.strategy == "review"
    assert result.execution.inference_count == 3
