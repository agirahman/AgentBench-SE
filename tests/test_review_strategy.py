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
    # Each revision is now re-reviewed, so a rejected revision cannot be shipped
    # unverified: 1 planner + 1 executor + 1 reviewer, then (executor + reviewer)
    # per revision turn.
    assert result.execution.inference_count == 3 + 2 * rev_mod.Config.MAX_REVISION_TURNS
    assert result.evaluation.success is True
    assert result.evaluation.error == ""


class ChangingExecutorProvider(DummyProvider):
    """Executor returns a DIFFERENT patch on revision; reviewer always rejects."""

    def __init__(self):
        super().__init__()
        self.executor_calls = 0

    def generate(self, prompt, role=""):
        self.calls.append((role, prompt))
        if role == "planner":
            return DummyInference("plan", role)
        if role == "executor":
            self.executor_calls += 1
            text = "initial-patch" if self.executor_calls == 1 else "revision-patch"
            return DummyInference(text, role)
        return DummyInference('{"verdict": "NEEDS_REVISION"}', role)


def test_rejected_revision_does_not_replace_the_initial_patch(monkeypatch):
    """A revision nobody approved must not be shipped just because it came later.

    Measured on EXP-20260927-007 (django-10924): the executor's revision after a
    NEEDS_REVISION verdict was what got evaluated, and it failed, while planning's
    single patch on the same issue resolved. The old code shipped whatever the
    working tree held last; it now ships a patch the reviewer actually approved,
    falling back to the first attempt when every candidate was rejected.
    """
    provider = ChangingExecutorProvider()
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
    patch, _result = strategy.run(issue)

    assert provider.executor_calls == 2, "the revision loop should have run once"
    assert patch.response == "initial-patch"


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
