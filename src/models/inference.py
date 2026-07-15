from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class InferenceResult:
    """Hasil tunggal dari satu pemanggilan LLM provider.

    Menggantikan return type ``str`` dari provider.generate().
    Seluruh metadata (usage, timing, finish_reason, model) dibawa
    oleh objek ini sehingga tidak ada state tersembunyi (last_usage).
    """

    role: str  # "planner" | "executor" | "reviewer"
    response: str
    usage: Optional[dict] = None  # {prompt_tokens, completion_tokens, total_tokens}
    execution_time: float = 0.0
    finish_reason: str = ""  # "STOP" | "MAX_TOKENS" | "stop" | ...
    model: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    @property
    def prompt_tokens(self) -> int:
        return (self.usage or {}).get("prompt_tokens", 0)

    @property
    def completion_tokens(self) -> int:
        return (self.usage or {}).get("completion_tokens", 0)

    @property
    def total_tokens(self) -> int:
        return (self.usage or {}).get("total_tokens", 0)


@dataclass
class InferenceRun:
    """Kumpulan semua InferenceResult dalam satu eksekusi strategi."""

    patch: str
    inferences: list[InferenceResult] = field(default_factory=list)

    @property
    def total_time(self) -> float:
        return sum(inf.execution_time for inf in self.inferences)

    @property
    def total_tokens(self) -> int:
        return sum(inf.total_tokens for inf in self.inferences)

    @property
    def total_prompt_tokens(self) -> int:
        return sum(inf.prompt_tokens for inf in self.inferences)

    @property
    def total_completion_tokens(self) -> int:
        return sum(inf.completion_tokens for inf in self.inferences)
