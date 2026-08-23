from models.issue import Issue
from strategies.review_strategy import ReviewStrategy, _extract_verdict


class DummyInference:
    def __init__(self, response, role):
        self.response = response
        self.role = role
        self.usage = {}
        self.finish_reason = "STOP"
        self.model = "demo-model"
        self.prompt_tokens = 0
        self.cached_tokens = 0
        self.regular_input_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.timestamp = ""


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


class NeedsRevisionProvider(DummyProvider):
    def generate(self, prompt, role=""):
        self.calls.append((role, prompt))
        if role == "planner":
            return DummyInference("plan", role)
        if role == "executor":
            return DummyInference("patch", role)
        return DummyInference('{"verdict": "NEEDS_REVISION"}', role)


def test_review_strategy_revision_loop_bounded_by_max_turns(monkeypatch):
    import strategies.review_strategy as rev_mod

    provider = NeedsRevisionProvider()
    strategy = ReviewStrategy(provider)

    monkeypatch.setattr(
        "agents.base.load_prompt_or_default",
        lambda filename, default="": str(default),
    )

    issue = Issue(
        instance_id="ISSUE-1",
        repo="django/django",
        base_commit="abc123",
        problem_statement="Bug",
    )
    patch, result = strategy.run(issue)

    assert patch.response == "patch"
    assert result.strategy == "review"
    assert result.execution.inference_count == 3 + rev_mod.Config.MAX_REVISION_TURNS
    assert result.evaluation.success is True
    assert result.evaluation.error == ""


def test_review_strategy_uses_fallback_prompt_when_template_missing(monkeypatch):
    provider = DummyProvider()
    strategy = ReviewStrategy(provider)

    monkeypatch.setattr(
        "agents.base.load_prompt_or_default",
        lambda filename, default="": str(default),
    )

    issue = Issue(
        instance_id="ISSUE-1",
        repo="django/django",
        base_commit="abc123",
        problem_statement="Bug",
    )
    patch, result = strategy.run(issue)

    assert patch.response == "patch"
    assert result.strategy == "review"
    assert result.execution.inference_count == 3
