from dataclasses import dataclass

from config import Config


DIFFICULTY_MAP = {
    "django/django": "hard",
    "sympy/sympy": "hard",
    "scikit-learn/scikit-learn": "medium",
    "matplotlib/matplotlib": "medium",
    "psf/requests": "easy",
    "mwaskom/seaborn": "easy",
}


@dataclass
class Issue:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints: str = ""

    @property
    def difficulty(self) -> str:
        return DIFFICULTY_MAP.get(self.repo, "unknown")

    def to_prompt(self) -> str:
        return (
            f"Instance ID: {self.instance_id}\n\n"
            f"Problem Statement:\n{self.problem_statement}"
        )

    def to_agent_prompt(self) -> str:
        base = self.to_prompt()
        if not Config.SOURCE_CONTEXT_ENABLED:
            return base
        from source_context import build_source_context

        context = build_source_context(self)
        if not context:
            return base
        return f"{base}\n\n===== SOURCE CODE (base commit) =====\n{context}"