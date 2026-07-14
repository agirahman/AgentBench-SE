from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class ExperimentResult:
    instance_id: str
    strategy: str
    model: str = ""
    execution_time: float = 0.0
    inference_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    patch_preview: str = ""
    error: str = ""
    timestamp: str = ""
