from dataclasses import dataclass


@dataclass
class Issue:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints: str = ""

    def to_prompt(self) -> str:
        return (
            f"Instance ID: {self.instance_id}\n\n"
            f"Problem Statement:\n{self.problem_statement}"
        )