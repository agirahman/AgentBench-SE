import errno
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path


INDEX_FILE = Path("results/experiment_index.json")

# Lock file lives next to the index; named with .lock suffix.
_LOCK_SUFFIX = ".lock"
_LOCK_RETRIES = 20
_LOCK_RETRY_DELAY = 0.1  # seconds between retries
# A lock older than this is treated as abandoned and broken. The critical
# section is a sub-millisecond read-modify-write, so no legitimate holder is
# ever near this age. Without it, a run killed while holding the lock (Ctrl+C,
# a crash, a reboot) would leave the file behind and EVERY later run would fail
# after 2 seconds of retries — a 10-hour experiment would die at startup with a
# confusing "could not acquire lock" error.
_STALE_LOCK_SECONDS = 60.0


def _break_stale_lock(lock_path: Path) -> bool:
    """Remove *lock_path* if it is old enough to be abandoned.

    Returns True if a stale lock was removed. Uses mtime rather than the PID
    because the holder may be on another machine sharing the directory.
    """
    try:
        age = time.time() - lock_path.stat().st_mtime
    except OSError:
        return False  # already gone
    if age < _STALE_LOCK_SECONDS:
        return False
    try:
        lock_path.unlink()
    except OSError:
        return False
    return True


def _acquire_lock(lock_path: Path) -> int:
    """Open *lock_path* exclusively (O_CREAT|O_EXCL) and return the fd.

    Retries up to _LOCK_RETRIES times with a short sleep between attempts so
    that two processes starting simultaneously do not both fail — one will
    always win the race and the other will retry until the lock is released.

    A lock left behind by a dead holder is broken after _STALE_LOCK_SECONDS so
    an interrupted run cannot block every future run.

    Raises RuntimeError if all retries are exhausted.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(_LOCK_RETRIES):
        try:
            fd = os.open(
                str(lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            return fd
        except OSError as exc:
            # Windows reports a lost O_CREAT|O_EXCL race as EACCES (13) rather
            # than EEXIST, because the loser is denied access to a file the
            # winner just created. Treating only EEXIST as "retry" made the
            # loser raise instead of waiting, so two simultaneous runs could
            # still fail — measured by test_concurrent_ids_are_unique. Both
            # codes mean "someone else holds it", so both must retry.
            if exc.errno not in (errno.EEXIST, errno.EACCES, errno.EPERM):
                raise
            _break_stale_lock(lock_path)
        time.sleep(_LOCK_RETRY_DELAY)
    raise RuntimeError(
        f"Could not acquire experiment-id lock after "
        f"{_LOCK_RETRIES} attempts: {lock_path}"
    )


def _release_lock(fd: int, lock_path: Path) -> None:
    try:
        os.close(fd)
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


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

    The read-modify-write on ``index_file`` is protected by an exclusive
    lock file (O_CREAT|O_EXCL) so two processes starting simultaneously
    cannot generate the same ID.
    """
    # Use UTC for the date component so experiment IDs are timezone-stable
    # (a run started just before local midnight still belongs to the same UTC day).
    today = datetime.now(timezone.utc).strftime("%Y%m%d")

    lock_path = index_file.with_suffix(_LOCK_SUFFIX)
    fd = _acquire_lock(lock_path)
    try:
        index = _load_index(index_file)
        counter = int(index.get(today, 0)) + 1
        index[today] = counter
        _save_index(index_file, index)
    finally:
        _release_lock(fd, lock_path)

    return f"EXP-{today}-{counter:03d}"


def create_experiment_dir(base: str, experiment_id: str) -> Path:
    """Create ``<base>/<experiment_id>/{artifacts,logs}`` and return path."""
    path = Path(base) / experiment_id
    (path / "artifacts").mkdir(parents=True, exist_ok=True)
    (path / "logs").mkdir(parents=True, exist_ok=True)
    return path
