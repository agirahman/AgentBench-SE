import json
from datetime import datetime
from pathlib import Path


INDEX_FILE = Path("results/experiment_index.json")


def _load_index(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_index(path: Path, index: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, sort_keys=True)


def generate_experiment_id(index_file: Path = INDEX_FILE) -> str:
    """Return a new experiment_id like ``EXP-20260715-001``.

    The counter increments per calendar day and is persisted to
    ``index_file`` so multiple runs in the same day produce distinct IDs.
    """
    today = datetime.now().strftime("%Y%m%d")
    index = _load_index(index_file)
    counter = int(index.get(today, 0)) + 1
    index[today] = counter
    _save_index(index_file, index)
    return f"EXP-{today}-{counter:03d}"


def create_experiment_dir(base: str, experiment_id: str) -> Path:
    """Create ``<base>/<experiment_id>/{artifacts,logs}`` and return path."""
    path = Path(base) / experiment_id
    (path / "artifacts").mkdir(parents=True, exist_ok=True)
    (path / "logs").mkdir(parents=True, exist_ok=True)
    return path
