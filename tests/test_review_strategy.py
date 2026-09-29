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
        # Strategies debit a task-level dollar budget with this; a double that
        # omits it is not faithful to InferenceResult.
        self.cost_usd = 0.0


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


# ---------------------------------------------------------------------------
# Verdict extraction must survive the shapes the reviewer ACTUALLY produced.
# Each string below is taken from a recorded run, so these are regressions for
# observed misreads, not hypothetical ones.
# ---------------------------------------------------------------------------

def test_extract_verdict_reads_json_after_prose():
    """EXP-20260929-022 django-11001/review: the reviewer said APPROVED, but only
    after a paragraph of prose. json.loads() failed on the whole string and the
    50-char fallback saw prose, so the run took a needless revision round."""
    response = (
        "The patch is verified correct against the actual source.\n\n"
        '**Mechanism confirmed from the code:**\n\n'
        'Old regex `r\'(.*)\\s(ASC|DESC)(.*)\'` had no DOTALL...\n\n'
        '{\n  "review_summary": "Verified in compiler.py",\n'
        '  "issues_found": ["None"],\n'
        '  "verdict": "APPROVED"\n}'
    )
    assert _extract_verdict(response) == "APPROVED"


def test_extract_verdict_reads_verdict_from_malformed_json():
    """EXP-20260928-001 django-10924/review: json.loads() raised
    "Expecting ',' delimiter: line 2 column 613" -- a missing comma deep inside a
    long field -- yet the verdict literal was intact."""
    response = (
        '{\n  "review_summary": "Verified the mechanism in source"\n'
        '  "issues_found": ["none"],\n  "verdict": "APPROVED"\n}'
    )
    assert _extract_verdict(response) == "APPROVED"


def test_extract_verdict_prefers_the_last_verdict():
    """The final message is authoritative: an early mention must not win."""
    response = (
        'I initially wrote "verdict": "APPROVED" but on re-reading the diff\n'
        'the guard is inverted, so the final answer is\n'
        '{"verdict": "NEEDS_REVISION"}'
    )
    assert _extract_verdict(response) == "NEEDS_REVISION"


def test_extract_verdict_does_not_approve_a_negated_mention():
    """The dangerous direction: a rejection that mentions APPROVED must not read
    as approval. The old fallback tested `"APPROVED" in text[:50]`, so a reviewer
    opening with "This is not APPROVED..." would ship an unreviewed patch."""
    response = "This is not APPROVED: the guard is inverted, so ordering breaks."
    assert _extract_verdict(response) == "NEEDS_REVISION"


def test_extract_verdict_still_approves_when_negation_is_not_of_the_verdict():
    """Guard against over-correcting: the negation rule must only fire on a
    negated APPROVED, not on any sentence that happens to contain a negation."""
    assert _extract_verdict("No issues found. APPROVED.") == "APPROVED"
    assert _extract_verdict("I cannot find any problem; APPROVED") == "APPROVED"


def test_extract_verdict_empty_and_missing_verdict_default_to_revision():
    assert _extract_verdict("") == "NEEDS_REVISION"
    # JSON that parses but carries no verdict: refuse rather than assume approval.
    assert _extract_verdict('{"review_summary": "looks fine"}') == "NEEDS_REVISION"


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
