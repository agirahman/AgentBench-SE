"""Self-contained provider health check for the setup wizard.

Performs a minimal inference ("Reply with only: OK") against the selected
provider using the API key. Providers that rely on optional third-party
libraries are imported lazily so the wizard still works (with a warning)
when those libraries are not installed.
"""

from __future__ import annotations

from typing import Tuple


def check_connection(
    provider: str, api_key: str, model: str
) -> Tuple[bool, str]:
    """Attempt a minimal health check. Returns (ok, message)."""
    if provider == "gemini":
        return _check_gemini(api_key, model)
    # openrouter, groq, opencode all use an OpenAI-compatible client
    return _check_openai_compatible(provider, api_key, model)


def _check_openai_compatible(
    provider: str, api_key: str, model: str
) -> Tuple[bool, str]:
    try:
        from openai import OpenAI
    except ImportError:
        return False, (
            "Library 'openai' is not installed in this environment; "
            "connection test skipped. It will be tested on first run."
        )
    try:
        base_url = {
            "openrouter": "https://openrouter.ai/api/v1",
            "groq": "https://api.groq.com/openai/v1",
            "opencode": None,  # opencode is API-key-less / local
        }.get(provider)
        if provider == "opencode":
            return True, "Opencode runs a local/self-hosted agent; no remote test."
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=30)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with only: OK"}],
            max_tokens=5,
            timeout=30,
        )
        text = (resp.choices[0].message.content or "").strip()
        return True, f"We got reply: {text!r}"
    except Exception as e:  # noqa: BLE001 - surface any provider error
        return False, str(e)


def _check_gemini(api_key: str, model: str) -> Tuple[bool, str]:
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return False, (
            "Library 'google-genai' is not installed in this environment."
        )
    try:
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=model,
            contents="Reply with only: OK",
            config=types.GenerateContentConfig(max_output_tokens=5),
        )
        return True, f"We got reply: {resp.text!r}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)