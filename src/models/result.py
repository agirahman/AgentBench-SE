from dataclasses import dataclass, field
from datetime import datetime, timezone
from .inference import InferenceRun, InferenceResult


@dataclass
class ExecutionResult:
    """Eksekusi strategi: delegasi ke ``InferenceRun`` untuk aggregate metrics."""

    run: InferenceRun

    @property
    def inference_count(self) -> int:
        return len(self.run.inferences)

    @property
    def execution_time(self) -> float:
        return self.run.total_time

    @property
    def prompt_tokens(self) -> int:
        return self.run.total_prompt_tokens

    @property
    def completion_tokens(self) -> int:
        return self.run.total_completion_tokens

    @property
    def total_tokens(self) -> int:
        return self.run.total_tokens

    @property
    def patch(self) -> str:
        return self.run.patch

    @property
    def patch_preview(self) -> str:
        return self.run.patch[:100] if self.run.patch else ""

    @property
    def inferences(self) -> list[InferenceResult]:
        return self.run.inferences


@dataclass
class CostSummary:
    """Agregasi biaya (USD + IDR) untuk satu eksekusi."""

    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float
    total_cost_idr: float
    pricing_version: str = ""


@dataclass
class EvaluationResult:
    """Hasil evaluasi satu eksekusi: success/error/etc."""

    success: bool = True
    error: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class ExperimentResult:
    """Top-level result untuk satu issue × strategi.

    Pure nested references — no flat duplicate fields.
    CSV flattening handled by exporter.
    """

    instance_id: str
    strategy: str
    model: str
    execution: ExecutionResult
    cost: CostSummary
    evaluation: EvaluationResult
    difficulty: str = ""
