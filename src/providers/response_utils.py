from models.inference import InferenceResult
from utils.logger import logger


def build_openai_inference_result(response, *, role: str = "", model: str = "", elapsed: float = 0.0) -> InferenceResult:
    """Safely normalize OpenAI-compatible provider responses into InferenceResult."""
    usage = getattr(response, "usage", None)
    prompt_t = getattr(usage, "prompt_tokens", 0) or 0
    comp_t = getattr(usage, "completion_tokens", 0) or 0
    total_t = getattr(usage, "total_tokens", 0) or 0

    choices = getattr(response, "choices", None) or []
    content = ""
    finish = ""

    if choices:
        choice = choices[0]
        finish = getattr(choice, "finish_reason", "") or ""
        message = getattr(choice, "message", None)
        if message is not None:
            content = getattr(message, "content", "") or ""
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, str):
                        parts.append(part)
                    elif isinstance(part, dict):
                        text = part.get("text") or ""
                        if isinstance(text, str):
                            parts.append(text)
                content = "".join(parts)
            elif not isinstance(content, str):
                content = str(content)
    else:
        finish = "EMPTY_RESPONSE"
        logger.warning(f"Provider returned no choices for role={role!r} model={model!r}")

    return InferenceResult(
        role=role,
        response=content,
        usage={
            "prompt_tokens": prompt_t,
            "completion_tokens": comp_t,
            "total_tokens": total_t,
        },
        execution_time=elapsed,
        finish_reason=finish,
        model=model,
    )
