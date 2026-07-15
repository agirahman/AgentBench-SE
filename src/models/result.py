from dataclasses import dataclass, field
from datetime import datetime, timezone
from .inference import InferenceRun


@dataclass
class ExperimentResult:
    """Hasil eksperimen untuk satu issue × satu strategi.

    Dibangun dari ``InferenceRun`` yang berisi semua langkah inference
    (planner, executor, reviewer) beserta final patch.
    Field komputasional (execution_time, inference_count, tokens)
    otomatis terisi dari ``run`` via ``__post_init__``.
    """

    instance_id: str
    strategy: str
    model: str
    run: InferenceRun
    execution_time: float = 0.0
    inference_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    patch_preview: str = ""
    error: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if self.run and not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
            self.execution_time = self.run.total_time
            self.inference_count = len(self.run.inferences)
            self.prompt_tokens = self.run.total_prompt_tokens
            self.completion_tokens = self.run.total_completion_tokens
            self.total_tokens = self.run.total_tokens
            self.patch_preview = self.run.patch[:100]
