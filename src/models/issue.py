from dataclasses import dataclass


@dataclass
class Issue:
    title: str
    description: str

    def to_prompt(self) -> str:
        return f"""
Title:
{self.title}

Description:
{self.description}
""".strip()