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
    cached_input_tokens: int = 0
    regular_input_tokens: int = 0
    cached_input_cost_usd: float = 0.0
    regular_input_cost_usd: float = 0.0
    peak_total_cost_usd: float = 0.0
    peak_total_cost_idr: float = 0.0
    actual_cost_usd: float = 0.0
    actual_cost_idr: float = 0.0
    # True when ANY request of this run was served from the provider's semantic
    # (response) cache instead of being generated. Such a run did not measure the
    # strategy, so it must be excluded from cross-strategy comparison rather than
    # averaged in. This is NOT the same as ``cached_input_tokens`` (prefix cache),
    # which is a normal, expected discount on a shared prompt prefix.
    semantic_cache_hit: bool = False
    semantic_cache_cost_saved_usd: float = 0.0
    # How many REQUESTS in this run were served from the response cache. The
    # boolean answers "is this run tainted?"; this answers "how badly?" -- one
    # repeated request and a fully replayed run are different findings.
    semantic_cache_hit_turns: int = 0


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
    patch_status: str = "VALID"
    # Semantic counterpart to ``patch_status``: whether the patch can actually be
    # applied to the target repo (APPLYABLE | NEEDS_FUZZ | NOT_APPLYABLE |
    # UNKNOWN). ``patch_status`` only proves the diff arithmetic is well formed.
    apply_status: str = "UNKNOWN"
    thinking: bool = False
    max_tokens: int = 0
