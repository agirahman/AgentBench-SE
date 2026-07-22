from pathlib import Path

PROMPT_DIR = Path(__file__).parent.parent / "prompts"


def load_prompt(filename: str) -> str:
    path = PROMPT_DIR / filename

    if not path.exists():
        raise FileNotFoundError(path)

    return path.read_text(encoding="utf-8")


def load_prompt_or_default(filename: str, default: str = "") -> str:
    try:
        return load_prompt(filename)
    except FileNotFoundError:
        return default