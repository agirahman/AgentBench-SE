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


def prompt_exists(filename: str) -> bool:
    """Whether a prompt file is actually on disk.

    Separate from ``load_prompt_or_default`` on purpose: that function returns its
    default both when the file is MISSING and when a caller has replaced it (tests
    stub it out to keep real prompt text out of assertions). Reading "empty result"
    as "file missing" makes those stubs emit a false "PROMPT VARIANT MISSING"
    warning, which is worse than no warning at all -- it points at a file that is
    present and sends the reader chasing a bug that does not exist. Ask the
    filesystem the question you actually mean.
    """
    return (PROMPT_DIR / filename).exists()