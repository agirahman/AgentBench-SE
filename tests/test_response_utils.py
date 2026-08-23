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


def test_config_defaults():
    assert Config.MAX_TOKENS > 0
    assert Config.API_TIMEOUT > 0
    assert isinstance(Config.DEEPSEEK_THINKING, bool)
    assert Config.DEEPSEEK_REASONING_EFFORT in ("low", "medium", "high")
    assert Config.MAX_REVISION_TURNS >= 1


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("MAX_TOKENS", "8192")
    monkeypatch.setenv("API_TIMEOUT", "120")
    monkeypatch.setenv("DEEPSEEK_THINKING", "true")
    monkeypatch.setenv("DEEPSEEK_REASONING_EFFORT", "medium")
    monkeypatch.setenv("MAX_REVISION_TURNS", "2")
    import importlib
    import config as config_module
    reloaded = importlib.reload(config_module)
    assert reloaded.Config.MAX_TOKENS == 8192
    assert reloaded.Config.API_TIMEOUT == 120
    assert reloaded.Config.DEEPSEEK_THINKING is True
    assert reloaded.Config.DEEPSEEK_REASONING_EFFORT == "medium"
    assert reloaded.Config.MAX_REVISION_TURNS == 2
