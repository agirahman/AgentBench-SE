from dataclasses import dataclass


@dataclass
class ExperimentResult:
    instance_id: str
    strategy: str
    execution_time: float
    inference_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    patch_preview: str
    error: str = ""
