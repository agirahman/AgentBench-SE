"""Failures must be labelled by CAUSE, not stamped with one catch-all status.

Every unhandled exception in runner.py used to be recorded as
`patch_status: "TIMEOUT"`. So a provider 502, an HTTP 429, and a git failure all
read as "the run ran out of time", which is a different thing entirely -- and the
difference decides whether a run is worth retrying and whether its result says
anything about the strategy.

Measured:
  * EXP-20260929-022 django-11019/review: a 502 (ENOTFOUND opencode.ai) with
    api_turns=1 was recorded as TIMEOUT.
  * EXP-20260824-005: 11 consecutive 429s and a git error, also TIMEOUT.

These tests pin the classifier and the status it produces.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from evaluation.retry import is_provider_error, is_rate_limit_error  # noqa: E402


class _Err(Exception):
    def __init__(self, msg, status_code=None):
        super().__init__(msg)
        self.status_code = status_code


# --- the real failure text from EXP-20260929-022 django-11019/review ---------

REAL_502 = (
    "Error code: 502 - {'error': {'message': '[502]: fetch failed "
    "(cause: ENOTFOUND: getaddrinfo ENOTFOUND opencode.ai)', "
    "'type': 'server_error', 'code': 'bad_gateway'}}"
)


def test_real_502_is_a_provider_error_not_a_timeout():
    assert is_provider_error(_Err(REAL_502)) is True
    # and it must NOT be mistaken for a rate limit (different remedy)
    assert is_rate_limit_error(_Err(REAL_502)) is False


def test_typed_5xx_status_is_a_provider_error():
    assert is_provider_error(_Err("boom", status_code=500)) is True
    assert is_provider_error(_Err("boom", status_code=503)) is True


def test_rate_limit_stays_a_rate_limit():
    """A 429 must not be reclassified as a generic provider error."""
    assert is_rate_limit_error(_Err("429 Too Many Requests")) is True


def test_client_error_is_not_a_provider_error():
    """A 4xx (other than 429) is our bug and must stay visible as one."""
    assert is_provider_error(_Err("400 Bad Request: invalid tool schema")) is False
    assert is_provider_error(_Err("invalid request", status_code=400)) is False


def test_ordinary_bug_is_not_a_provider_error():
    assert is_provider_error(_Err("KeyError: 'path'")) is False
    assert is_provider_error(ValueError("bad diff")) is False


def test_bare_digits_in_a_message_do_not_trip_the_marker():
    """Regression guard: a naive '502' substring marker fires on any number.

    A message that merely CONTAINS 502 (a token count, a line number) must not be
    read as a gateway error.
    """
    assert is_provider_error(_Err("patch at line 502 did not apply")) is False
    assert is_provider_error(_Err("spent 5021 tokens")) is False


def test_network_failures_are_provider_errors():
    for msg in (
        "Connection error.",
        "ConnectionResetError: [Errno 104] Connection reset by peer",
        "requests.exceptions.ConnectionError: HTTPSConnectionPool: Read timed out",
        "httpx.ConnectError: [Errno -2] Name or service not known",
        "fetch failed",
    ):
        assert is_provider_error(_Err(msg)) is True, msg
