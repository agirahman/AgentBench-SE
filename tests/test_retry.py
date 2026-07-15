import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evaluation.retry import with_retry


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
