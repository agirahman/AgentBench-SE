import types

import pytest

from config import Config
from providers.response_utils import (
    build_openai_inference_result,
    create_completion_with_headers,
    extract_semantic_cache,
)
from models.inference import InferenceResult

# The response-cache signal travels in HTTP headers, so the tests below drive the
# REAL OpenAI SDK through httpx.MockTransport (no network). That matters: an
# earlier version of this file only built fake response objects by hand, so it
# stayed green while the feature was dead in production -- the SDK's
# ChatCompletion has NO ``response_headers`` field, so
# ``getattr(response, "response_headers", None)`` was always None and the whole
# header-parsing block never executed.

CACHE_HIT_HEADERS = {"x-omniroute-cache-hit": "true",
                     "x-omniroute-cost-saved": "0.0042"}
CACHE_MISS_HEADERS = {"x-omniroute-cache-hit": "false"}

_COMPLETION_BODY = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 1,
    "model": "test-model",
    "choices": [
        {"index": 0, "finish_reason": "stop",
         "message": {"role": "assistant", "content": "hello"}}
    ],
    "usage": {"prompt_tokens": 1000, "completion_tokens": 5, "total_tokens": 1005},
}


def _sdk_client(headers):
    """A real OpenAI client whose transport returns canned headers (no network)."""
    import httpx
    from openai import OpenAI

    def handler(request):
        return httpx.Response(200, json=_COMPLETION_BODY, headers=headers)

    return OpenAI(
        api_key="test",
        base_url="http://test.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_sdk_plain_create_loses_the_cache_header():
    """Document WHY the raw path is required: the parsed model has no header field.

    This is the defect, pinned. If a future SDK starts exposing the headers on the
    parsed object this test fails -- which is the correct moment to revisit
    ``create_completion_with_headers`` rather than leave dead code in place.
    """
    client = _sdk_client(CACHE_HIT_HEADERS)
    parsed = client.chat.completions.create(
        model="test-model", messages=[{"role": "user", "content": "hi"}]
    )
    assert not hasattr(parsed, "response_headers"), (
        "the SDK now exposes response_headers on the parsed completion; the "
        "with_raw_response workaround in create_completion_with_headers may no "
        "longer be necessary"
    )


def test_create_completion_with_headers_captures_real_sdk_headers():
    """END-TO-END: the header must survive a real SDK round trip.

    Goes RED if ``create_completion_with_headers`` stops using
    ``with_raw_response`` -- the exact Lapis-2 bug, where the signal was silently
    unreadable and every run looked cache-clean.
    """
    client = _sdk_client(CACHE_HIT_HEADERS)
    response = create_completion_with_headers(
        client, model="test-model", messages=[{"role": "user", "content": "hi"}]
    )

    headers = getattr(response, "response_headers", None)
    assert headers, (
        "the HTTP headers were not attached; without them the response-cache "
        "signal is unreadable and every run reports 'no hit'"
    )
    hit, saved = extract_semantic_cache(headers)
    assert hit is True
    assert saved == pytest.approx(0.0042)

    # The parsed content must still be a normal completion.
    assert response.choices[0].message.content == "hello"
    assert response.usage.total_tokens == 1005


def test_build_result_reads_semantic_hit_from_sdk_response():
    """The full chain: real SDK -> build_openai_inference_result -> usage flag."""
    client = _sdk_client(CACHE_HIT_HEADERS)
    response = create_completion_with_headers(
        client, model="test-model", messages=[{"role": "user", "content": "hi"}]
    )
    result = build_openai_inference_result(
        response, role="direct", model="test-model",
        response_headers=getattr(response, "response_headers", None),
    )
    assert result.usage["semantic_cache_hit"] is True
    assert result.usage["semantic_cache_cost_saved_usd"] == pytest.approx(0.0042)


def test_sdk_response_without_hit_headers_is_not_flagged():
    client = _sdk_client(CACHE_MISS_HEADERS)
    response = create_completion_with_headers(
        client, model="test-model", messages=[{"role": "user", "content": "hi"}]
    )
    result = build_openai_inference_result(
        response, role="direct", model="test-model",
        response_headers=getattr(response, "response_headers", None),
    )
    assert result.usage["semantic_cache_hit"] is False


def test_sdk_error_still_raises_so_retry_still_works():
    """with_raw_response must not swallow errors: call_with_retry needs them.

    If the raw path returned the error body instead of raising, a 429 would look
    like a successful call and the retry/backoff path would never run.
    """
    import httpx
    from openai import OpenAI

    def handler(request):
        return httpx.Response(
            429,
            json={"error": {"message": "slow down", "type": "rate_limit_error"}},
            headers=CACHE_HIT_HEADERS,
        )

    client = OpenAI(api_key="test", base_url="http://test.invalid/v1",
                    http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(Exception) as excinfo:
        create_completion_with_headers(
            client, model="test-model", messages=[{"role": "user", "content": "hi"}]
        )
    assert "rate" in type(excinfo.value).__name__.lower(), (
        f"expected a rate-limit error, got {type(excinfo.value).__name__}"
    )


def test_create_completion_falls_back_for_clients_without_raw_response():
    """Fake/limited clients must keep working (they simply carry no headers)."""

    class _Completions:
        def create(self, **kwargs):
            return types.SimpleNamespace(ok=True)

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=_Completions())
    )
    out = create_completion_with_headers(client, model="m", messages=[])
    assert out.ok is True


# ---------------------------------------------------------------------------
# The ``response_headers`` fallback: providers that pass only the response
# ---------------------------------------------------------------------------
# ``build_openai_inference_result`` can be called WITHOUT ``response_headers``; it
# then reads the attribute off the response object. That fallback is load-bearing:
# ``openrouter_provider``, ``deepseek_provider`` and ``groq_provider`` all call it
# that way, so for those routes the fallback is the ONLY path the signal can take.
# These tests pin it so a refactor cannot quietly drop it.

def test_fallback_reads_headers_attached_to_the_response_object():
    """No ``response_headers`` argument -> read it off the response. HIT survives."""
    resp = _make_response([_make_choice(content="hi")], prompt=1000, comp=5)
    resp.response_headers = {"x-omniroute-cache-hit": "true",
                             "x-omniroute-cost-saved": "0.007"}

    result = build_openai_inference_result(resp, role="direct", model="m")

    assert result.usage["semantic_cache_hit"] is True, (
        "the fallback path must carry the signal; providers that call this "
        "function without response_headers depend on it"
    )
    assert result.usage["semantic_cache_cost_saved_usd"] == pytest.approx(0.007)


def test_fallback_without_the_attribute_is_not_a_hit():
    """A response with no headers attached at all must stay clean, not crash."""
    resp = _make_response([_make_choice(content="hi")])
    assert not hasattr(resp, "response_headers")

    result = build_openai_inference_result(resp, role="direct", model="m")

    assert result.usage["semantic_cache_hit"] is False
    assert result.usage["semantic_cache_cost_saved_usd"] == 0.0


def test_explicit_headers_win_over_the_response_attribute():
    """An explicit argument must not be silently overridden by the attribute."""
    resp = _make_response([_make_choice(content="hi")])
    resp.response_headers = {"x-omniroute-cache-hit": "false"}

    result = build_openai_inference_result(
        resp, role="direct", model="m",
        response_headers={"x-omniroute-cache-hit": "true"},
    )
    assert result.usage["semantic_cache_hit"] is True, (
        "the explicit response_headers argument must take precedence"
    )


def test_openrouter_generate_carries_the_cache_signal(monkeypatch):
    """END-TO-END through OpenRouterProvider.generate -- the real call site.

    OpenRouter is the provider that calls ``build_openai_inference_result`` with
    only the response object, so this exercises the fallback exactly as production
    does. A real OpenAI client + mock transport is used, so the header can only be
    reached through the raw-response path; a hand-built fake response would not
    prove anything about the wiring.
    """
    import httpx
    from openai import OpenAI

    from providers.openrouter_provider import OpenRouterProvider

    body = {
        "id": "chatcmpl-or", "object": "chat.completion", "created": 1,
        "model": "openrouter/test",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": '{"patch": "x"}'}}],
        "usage": {"prompt_tokens": 500, "completion_tokens": 5, "total_tokens": 505},
    }

    def handler(request):
        return httpx.Response(200, json=body,
                              headers={"x-omniroute-cache-hit": "true",
                                       "x-omniroute-cost-saved": "0.011"})

    # Bypass __init__ (it requires an API key from .env); only the client matters.
    provider = OpenRouterProvider.__new__(OpenRouterProvider)
    provider.model = "openrouter/test"
    provider.client = OpenAI(
        api_key="test", base_url="http://test.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    # generate() is wrapped by @with_retry, which reads Config.MAX_RETRIES.
    import evaluation.retry as retry_mod
    monkeypatch.setattr(retry_mod.Config, "MAX_RETRIES", 1)
    monkeypatch.setattr(retry_mod.Config, "RATE_LIMIT_BACKOFF_BASE", 0.0)
    monkeypatch.setattr(retry_mod.Config, "RATE_LIMIT_BACKOFF_MAX", 0.0)
    monkeypatch.setattr(retry_mod.time, "sleep", lambda _s: None)

    result = provider.generate("fix it", role="direct")

    assert result.usage is not None
    assert result.usage.get("semantic_cache_hit") is True, (
        "OpenRouterProvider.generate did not carry the response-cache signal; "
        f"usage={result.usage}"
    )
    assert result.usage.get("semantic_cache_cost_saved_usd") == pytest.approx(0.011)


def test_semantic_hit_promotes_cached_tokens_to_full_prompt():
    """On a hit the whole prompt was served from cache -- but this is NOT the gate.

    ``cached_tokens == prompt_tokens`` is produced by a semantic hit AND by a fully
    prefix-cached call, so it can never be used to DECIDE a hit. It is kept because
    it makes the cost/cache columns honest. The decision comes from the flag only.
    """
    usage = types.SimpleNamespace(prompt_tokens=1000, completion_tokens=5,
                                  total_tokens=1005,
                                  prompt_tokens_details=types.SimpleNamespace(
                                      cached_tokens=0))
    resp = types.SimpleNamespace(usage=usage, choices=[_make_choice(content="hi")])
    result = build_openai_inference_result(
        resp, role="direct", model="m",
        response_headers={"x-omniroute-cache-hit": "true"},
    )
    assert result.usage["cached_tokens"] == 1000, "promoted for honest cost metrics"
    assert result.usage["semantic_cache_hit"] is True, "flag, not the ratio, decides"


def test_full_prefix_cache_without_header_is_not_a_semantic_hit():
    """The mirror case: 100% cached and NO header must stay unflagged.

    Together with the test above this pins the discrimination rule: identical
    ``cached_tokens`` values, different verdicts -- so the verdict cannot come from
    the token counts.
    """
    usage = types.SimpleNamespace(prompt_tokens=1000, completion_tokens=5,
                                  total_tokens=1005,
                                  prompt_tokens_details=types.SimpleNamespace(
                                      cached_tokens=1000))
    resp = types.SimpleNamespace(usage=usage, choices=[_make_choice(content="hi")])
    result = build_openai_inference_result(resp, role="direct", model="m")
    assert result.usage["cached_tokens"] == 1000, "same token count as the hit case"
    assert result.usage["semantic_cache_hit"] is False, (
        "100% prefix caching is normal and must NOT be reported as a semantic hit"
    )


def _make_choice(content="", finish="stop", reasoning=""):
    message = types.SimpleNamespace(content=content)
    if reasoning:
        message.reasoning_content = reasoning
    return types.SimpleNamespace(message=message, finish_reason=finish)


def _make_response(choices, prompt=10, comp=5, total=15, cached=0):
    usage = types.SimpleNamespace(prompt_tokens=prompt, completion_tokens=comp, total_tokens=total, prompt_tokens_details=types.SimpleNamespace(cached_tokens=cached))
    return types.SimpleNamespace(usage=usage, choices=choices)


def test_normal_content_and_finish():
    resp = _make_response([_make_choice(content="hello", finish="stop")])
    result = build_openai_inference_result(resp, role="direct", model="m")
    assert isinstance(result, InferenceResult)
    assert result.response == "hello"
    assert result.finish_reason == "stop"
    assert result.reasoning_content == ""
    assert result.total_tokens == 15


def test_cached_tokens_captured_from_prompt_tokens_details():
    resp = _make_response([_make_choice(content="hi", finish="stop")], prompt=1000, cached=700)
    result = build_openai_inference_result(resp, role="direct", model="m")
    assert result.cached_tokens == 700
    assert result.regular_input_tokens == 300


def test_cached_tokens_fallback_deepseek_format():
    usage = types.SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=5,
        total_tokens=1005,
        prompt_cache_hit_tokens=800,
    )
    resp = types.SimpleNamespace(usage=usage, choices=[_make_choice(content="hi", finish="stop")])
    result = build_openai_inference_result(resp, role="direct", model="m")
    assert result.cached_tokens == 800
    assert result.regular_input_tokens == 200


def test_reasoning_content_captured_when_content_empty():
    resp = _make_response([_make_choice(content="", finish="length", reasoning="long chain of thought")])
    result = build_openai_inference_result(resp, role="direct", model="m")
    assert result.reasoning_content == "long chain of thought"
    assert result.response == ""
    assert result.finish_reason == "length"


def test_reasoning_with_nonempty_content():
    resp = _make_response([_make_choice(content='{"patch": "x"}', finish="stop", reasoning="thinking...")])
    result = build_openai_inference_result(resp, role="direct", model="m")
    assert result.reasoning_content == "thinking..."
    assert result.response == '{"patch": "x"}'


def test_content_as_list_of_parts():
    msg = types.SimpleNamespace(content=[{"text": "a"}, {"text": "b"}])
    choice = types.SimpleNamespace(message=msg, finish_reason="stop")
    resp = _make_response([choice])
    result = build_openai_inference_result(resp, role="direct", model="m")
    assert result.response == "ab"


def test_no_choices_marks_empty():
    resp = _make_response([])
    result = build_openai_inference_result(resp, role="direct", model="m")
    assert result.finish_reason == "EMPTY_RESPONSE"
    assert result.response == ""


def test_config_defaults(monkeypatch):
    """The shipped defaults must be sane.

    MAX_REVISION_TURNS is PINNED to the value .env ships. Reading it ambient made the
    test's outcome depend on the caller's shell: exporting MAX_REVISION_TURNS=0 made
    it fail, which reads as a code defect but is really the environment doing its
    documented job (a shell value overrides .env). A partner audit flagged this
    (docs/AUDIT_TEST_ROBUSTNESS.md, 1.2) -- the assertion is about the CONFIGURED
    default, so it should say so.

    That 0 is legal-but-starved is a separate, real property, pinned in
    tests/test_budget_modes.py::test_no_reserve_keeps_the_legacy_starved_behaviour.
    """
    from agents import budget as budget_mod
    from agents import base as base_mod
    from agents import registry as registry_mod
    from agents import tools as tools_mod
    from strategies import review_strategy

    for mod in (base_mod, budget_mod, registry_mod, tools_mod, review_strategy):
        monkeypatch.setattr(mod.Config, "MAX_REVISION_TURNS", 4)

    assert Config.MAX_TOKENS > 0
    assert Config.API_TIMEOUT > 0
    assert isinstance(Config.DEEPSEEK_THINKING, bool)
    assert Config.DEEPSEEK_REASONING_EFFORT in ("low", "medium", "high")
    assert Config.MAX_REVISION_TURNS >= 1


def test_config_from_env():
    """Env vars must reach Config at import time, and be undone afterwards.

    NO monkeypatch, and no fixture ordering. `importlib.reload(config)` installs a
    NEW Config class, so the env must be restored BEFORE the restoring reload -- and
    relying on fixture teardown order to arrange that is exactly what went wrong
    here: a fixture declared to the right of `monkeypatch` is torn down FIRST
    (pytest finalises in reverse), so it reloaded while the test's env values were
    still installed and the leak survived.

    Doing both steps in one `finally`, in the right order, removes the ordering
    question entirely. Verified load-bearing by tools/_prove_containment.py: with
    the restoring reload removed, test_config_values_are_restored_after_the_reload
    FAILS with `assert 8192 != 8192`.
    """
    import importlib
    import os

    import config as config_module

    overrides = {
        "MAX_TOKENS": "8192",
        "API_TIMEOUT": "120",
        "DEEPSEEK_THINKING": "true",
        "DEEPSEEK_REASONING_EFFORT": "medium",
        "MAX_REVISION_TURNS": "2",
    }
    before = {k: os.environ.get(k) for k in overrides}

    try:
        os.environ.update(overrides)
        reloaded = importlib.reload(config_module)
        assert reloaded.Config.MAX_TOKENS == 8192
        assert reloaded.Config.API_TIMEOUT == 120
        assert reloaded.Config.DEEPSEEK_THINKING is True
        assert reloaded.Config.DEEPSEEK_REASONING_EFFORT == "medium"
        assert reloaded.Config.MAX_REVISION_TURNS == 2
    finally:
        # Restore the environment FIRST, then reload, so the class the rest of the
        # session sees is built from the real .env rather than from this test.
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(config_module)


def test_config_values_are_restored_after_the_reload():
    """The reload must not leave the test's env values installed for the session.

    `test_config_from_env` sets MAX_TOKENS=8192, API_TIMEOUT=120 and
    MAX_REVISION_TURNS=2, then reloads config. Without the restore fixture,
    config.Config keeps THOSE values for every later test in the session -- a real
    leak, and a deterministic one.

    This checks that leak directly. A FIRST version of this test asserted instead
    that all modules share one Config class after the reload, and it was WRONG: it
    passed even with the fixture disabled, because whether the modules agree depends
    on import ORDER (a module imported after the reload picks up the new class). A
    check whose outcome depends on import order is not a check. Verified with
    tools/_prove_containment.py, which disables the fixture and confirms the failure.

    The class split is real and is proven in a fresh process by
    tools/check_config_reload_poison.py. It is worked around where it matters:
    tests/test_tool_loop_retry.py patches both module references on purpose.
    """
    from config import Config

    assert Config.MAX_TOKENS != 8192, (
        "config.Config still holds MAX_TOKENS=8192 from test_config_from_env; the "
        "reload was not undone and every later test reads a poisoned value"
    )
    assert Config.API_TIMEOUT != 120, (
        "config.Config still holds API_TIMEOUT=120 from test_config_from_env"
    )
    assert Config.MAX_REVISION_TURNS != 2, (
        "config.Config still holds MAX_REVISION_TURNS=2 from test_config_from_env"
    )
