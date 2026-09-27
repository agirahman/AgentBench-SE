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
    # The gold test patch from SWE-bench. Used to strip test files out of the
    # model's patch before evaluation: the harness resets test files and applies
    # this one, so a model patch that touches them can conflict (see
    # strip_test_files below).
    test_patch: str = ""

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

        # Source context and tool calling are two ways to give the agent the same
        # information: one passive (a pre-selected snapshot), one active (the agent
        # explores). Injecting both is a confound — the agent is handed a 41 KB
        # snapshot *and* told to explore, so "the agent found it itself" is no
        # longer true, and the snapshot is often irrelevant (for django-10914,
        # about FILE_UPLOAD_PERMISSIONS, it injected tests/admin_views/tests.py).
        # When tools are on, tools win.
        if Config.TOOLCALL_ENABLED:
            return base

        if not Config.SOURCE_CONTEXT_ENABLED:
            return base
        from source_context import build_source_context

        context = build_source_context(self)
        if not context:
            return base
        return f"{base}\n\n===== SOURCE CODE (base commit) =====\n{context}"