from config import Config, _get_env, _get_float_env, _get_int_env


def test_get_env_returns_default_when_blank(monkeypatch):
    monkeypatch.delenv("FOO_VALUE", raising=False)
    assert _get_env("FOO_VALUE", "fallback") == "fallback"

    monkeypatch.setenv("FOO_VALUE", "   ")
    assert _get_env("FOO_VALUE", "fallback") == "fallback"


def test_get_float_env_and_int_env_handle_invalid_values(monkeypatch):
    monkeypatch.setenv("FLOAT_VALUE", "not-a-number")
    monkeypatch.setenv("INT_VALUE", "abc")

    assert _get_float_env("FLOAT_VALUE", 1.5) == 1.5
    assert _get_int_env("INT_VALUE", 7) == 7


def test_config_uses_defaults_when_env_missing():
    assert Config.GEMINI_MODEL
    assert Config.OPENROUTER_MODEL
    assert Config.MAX_RETRIES >= 1
