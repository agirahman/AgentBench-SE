import types

import pytest

from config import Config
from providers.response_utils import build_openai_inference_result
from models.inference import InferenceResult


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
