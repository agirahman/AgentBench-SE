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
        """The prompt handed to an agent: the problem statement, nothing else.

        Deliberately carries NO pre-selected source snapshot. The agent is
        expected to explore the checked-out repo itself with read_file / grep /
        list_files, which is the whole point of tool calling.

        Injecting a snapshot as well is a confound, not a convenience: the agent
        would be handed the evidence *and* told to go find it, so the claim
        "the agent located the bug itself" would no longer be true. Measured on
        EXP-20260924-005 the injected block was also frequently irrelevant — for
        django-10914 (about FILE_UPLOAD_PERMISSIONS) it injected
        tests/admin_views/tests.py — while costing ~41 KB of context on every
        single agent call.

        ``source_context.build_source_context`` is retained as an ablation path
        (passive context vs active exploration); it is simply not used here.
        """
        return self.to_prompt()