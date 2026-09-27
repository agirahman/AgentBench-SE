import pytest

from evaluation.retry import is_rate_limit_error, with_retry


def test_success_first_attempt():
    calls = {"n": 0}

    @with_retry(max_retries=3, base_delay=0.0)
    def ok():
        calls["n"] += 1
        return "ok"

    assert ok() == "ok"
    assert calls["n"] == 1


def test_retries_then_succeeds():
    calls = {"n": 0}

    @with_retry(max_retries=3, base_delay=0.0)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise ConnectionError("boom")
        return "recovered"

    assert flaky() == "recovered"
    assert calls["n"] == 2


def test_raises_after_max_retries():
    calls = {"n": 0}

    @with_retry(max_retries=3, base_delay=0.0)
    def always_fail():
        calls["n"] += 1
        raise TimeoutError("nope")

    with pytest.raises(TimeoutError):
        always_fail()
    assert calls["n"] == 3


# ---------------------------------------------------------------------------
# Rate-limit (429) detection and backoff
# ---------------------------------------------------------------------------

class _StatusError(Exception):
    def __init__(self, msg, status_code=None):
        super().__init__(msg)
        self.status_code = status_code


def test_is_rate_limit_error_by_status_code():
    assert is_rate_limit_error(_StatusError("boom", status_code=429))


def test_is_rate_limit_error_by_message():
    assert is_rate_limit_error(RuntimeError("Error 429: too many requests"))
    assert is_rate_limit_error(RuntimeError("You've reached your 5-hour usage limit"))
    assert is_rate_limit_error(RuntimeError("RESOURCE_EXHAUSTED: quota exceeded"))


def test_is_rate_limit_error_false_for_ordinary_failures():
    assert not is_rate_limit_error(ConnectionError("connection reset by peer"))
    assert not is_rate_limit_error(TimeoutError("read timed out"))
    assert not is_rate_limit_error(RuntimeError("invalid api key (401)"))


def test_rate_limit_uses_longer_backoff(monkeypatch):
    """429 must sleep on the rate-limit schedule, not the 2s network schedule."""
    import evaluation.retry as retry_mod

    slept = []
    monkeypatch.setattr(retry_mod.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(retry_mod.Config, "RATE_LIMIT_BACKOFF_BASE", 60.0)
    monkeypatch.setattr(retry_mod.Config, "RATE_LIMIT_BACKOFF_MAX", 300.0)

    calls = {"n": 0}

    @with_retry(max_retries=3, base_delay=2.0)
    def limited():
        calls["n"] += 1
        raise _StatusError("429 rate limit", status_code=429)

    with pytest.raises(_StatusError):
        limited()

    assert calls["n"] == 3
    # attempt1 -> 60s, attempt2 -> 120s; never the 2s/4s network schedule.
    assert slept == [60.0, 120.0]


def test_rate_limit_backoff_is_capped(monkeypatch):
    import evaluation.retry as retry_mod

    slept = []
    monkeypatch.setattr(retry_mod.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(retry_mod.Config, "RATE_LIMIT_BACKOFF_BASE", 200.0)
    monkeypatch.setattr(retry_mod.Config, "RATE_LIMIT_BACKOFF_MAX", 300.0)

    @with_retry(max_retries=3, base_delay=0.0)
    def limited():
        raise _StatusError("429", status_code=429)

    with pytest.raises(_StatusError):
        limited()

    assert slept == [200.0, 300.0]  # second would be 400 -> capped at 300


def test_network_error_keeps_short_backoff(monkeypatch):
    import evaluation.retry as retry_mod

    slept = []
    monkeypatch.setattr(retry_mod.time, "sleep", lambda s: slept.append(s))

    @with_retry(max_retries=3, base_delay=2.0)
    def flaky():
        raise ConnectionError("reset")

    with pytest.raises(ConnectionError):
        flaky()

    assert slept == [2.0, 4.0]  # unchanged network schedule


# ---------------------------------------------------------------------------
# Runner circuit breaker: stop cleanly after consecutive 429s
# ---------------------------------------------------------------------------

def _issue(i):
    from models.issue import Issue
    # ``difficulty`` is a derived property (from repo), not a constructor field.
    return Issue(
        instance_id=f"repo__repo-{i}",
        repo="psf/requests",
        base_commit="a" * 40,
        problem_statement="p",
    )


def _run_with_failing_strategy(monkeypatch, tmp_path, exc, n_issues=4, limit=2):
    """Drive run_experiments with a strategy whose run() always raises."""
    from experiments import runner as runner_mod

    # Patch the Config *object runner holds*, not a fresh `from config import
    # Config`. Another test (test_response_utils) reloads the config module,
    # which rebinds config.Config to a new class while runner_mod.Config keeps
    # pointing at the original one. Patching the fresh import would silently miss
    # the object run_experiments actually reads.
    monkeypatch.setattr(runner_mod.Config, "RATE_LIMIT_CONSECUTIVE_LIMIT", limit)
    monkeypatch.setattr(runner_mod.Config, "APPLY_CHECK_ENABLED", False)
    monkeypatch.setattr(runner_mod, "generate_experiment_id", lambda: "EXP-test")
    monkeypatch.setattr(
        runner_mod, "create_experiment_dir",
        lambda base, eid: str(tmp_path / eid),
    )

    calls = {"n": 0}

    class FailingStrategy:
        def run(self, issue):
            calls["n"] += 1
            raise exc

    issues = [_issue(i) for i in range(n_issues)]
    runner_mod.run_experiments(
        issues=issues,
        strategies={"direct": FailingStrategy()},
        base_dir=str(tmp_path),
        provider_name="p",
        rate_limit_seconds=0.0,
        model="m",
    )
    return calls["n"]


def test_runner_stops_after_consecutive_rate_limits(monkeypatch, tmp_path):
    calls = _run_with_failing_strategy(
        monkeypatch, tmp_path, _StatusError("429 too many requests", status_code=429),
        n_issues=10, limit=2,
    )
    assert calls == 2  # stopped after the breaker tripped, not all 10


def test_runner_continues_on_non_rate_limit_errors(monkeypatch, tmp_path):
    calls = _run_with_failing_strategy(
        monkeypatch, tmp_path, ConnectionError("reset"), n_issues=4, limit=2,
    )
    assert calls == 4  # ordinary failures never trip the breaker
