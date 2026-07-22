from providers.response_utils import build_openai_inference_result


class _Choice:
    def __init__(self, content, finish_reason=""):
        self.finish_reason = finish_reason
        self.message = type("Message", (), {"content": content})()


class _Usage:
    def __init__(self, prompt=1, completion=2, total=3):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total


class _Response:
    def __init__(self, content="ok", finish_reason="", usage=None):
        self.choices = [_Choice(content, finish_reason)]
        self.usage = usage or _Usage()


def test_build_openai_inference_result_normalizes_content():
    response = _Response(content="hello")

    result = build_openai_inference_result(response, role="executor", model="demo")

    assert result.role == "executor"
    assert result.response == "hello"
    assert result.model == "demo"
    assert result.total_tokens == 3


def test_build_openai_inference_result_handles_empty_choices():
    response = type("Response", (), {"choices": [], "usage": None})()

    result = build_openai_inference_result(response, role="reviewer", model="demo")

    assert result.response == ""
    assert result.finish_reason == "EMPTY_RESPONSE"
    assert result.total_tokens == 0
