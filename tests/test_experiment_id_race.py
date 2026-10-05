"""Regression tests for the EXP-ID race condition fix.

Two processes calling generate_experiment_id() concurrently on the same
index_file must produce distinct IDs.  The fix uses O_CREAT|O_EXCL to
serialise the read-modify-write cycle, so even if both processes start at
exactly the same millisecond, the atomic OS open() guarantees exactly one
winner per lock acquisition.
"""

import concurrent.futures
import json
import tempfile
from pathlib import Path

import pytest

from experiment_id import (
    _LOCK_SUFFIX,
    _acquire_lock,
    _release_lock,
    generate_experiment_id,
)


class TestExperimentIdLock:
    def test_concurrent_ids_are_unique(self):
        """N threads calling generate_experiment_id() must all get distinct IDs."""
        with tempfile.TemporaryDirectory() as tmp:
            index_file = Path(tmp) / "experiment_index.json"
            n_workers = 8
            with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as ex:
                futs = [
                    ex.submit(generate_experiment_id, index_file)
                    for _ in range(n_workers)
                ]
                ids = [f.result() for f in futs]

        assert len(ids) == n_workers, "some workers raised an exception"
        assert len(set(ids)) == n_workers, (
            f"duplicate IDs detected: {sorted(ids)}"
        )

    def test_concurrent_counter_is_monotone(self):
        """After N concurrent calls the persisted counter must equal N."""
        with tempfile.TemporaryDirectory() as tmp:
            index_file = Path(tmp) / "experiment_index.json"
            n_workers = 6
            with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as ex:
                futs = [
                    ex.submit(generate_experiment_id, index_file)
                    for _ in range(n_workers)
                ]
                _ = [f.result() for f in futs]

            data = json.loads(index_file.read_text())
            today = list(data.keys())[0]
            assert data[today] == n_workers

    def test_lock_file_removed_after_success(self):
        """The .lock file must not linger after a successful ID generation."""
        with tempfile.TemporaryDirectory() as tmp:
            index_file = Path(tmp) / "experiment_index.json"
            generate_experiment_id(index_file)
            lock_path = index_file.with_suffix(_LOCK_SUFFIX)
            assert not lock_path.exists(), "stale .lock file left behind"

    def test_lock_acquire_release(self):
        """_acquire_lock and _release_lock are the low-level primitives."""
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "test.lock"
            fd = _acquire_lock(lock_path)
            assert lock_path.exists()
            _release_lock(fd, lock_path)
            assert not lock_path.exists()

    def test_second_acquire_blocks_then_succeeds(self):
        """A second acquire must wait for the first to release."""
        import threading

        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "test.lock"

            fd1 = _acquire_lock(lock_path)
            errors: list[Exception] = []

            def _try_acquire():
                try:
                    fd2 = _acquire_lock(lock_path)
                    _release_lock(fd2, lock_path)
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            # Release the first lock after a short delay so the second thread
            # must retry at least once.
            def _delayed_release():
                import time
                time.sleep(0.05)
                _release_lock(fd1, lock_path)

            t_rel = threading.Thread(target=_delayed_release)
            t_acq = threading.Thread(target=_try_acquire)
            t_rel.start()
            t_acq.start()
            t_rel.join()
            t_acq.join()

            assert not errors, f"second acquire failed: {errors}"

    def test_existing_tests_still_pass(self):
        """Smoke-check: original sequential contract still holds."""
        with tempfile.TemporaryDirectory() as tmp:
            index_file = Path(tmp) / "index.json"
            id1 = generate_experiment_id(index_file)
            id2 = generate_experiment_id(index_file)
            assert id1 != id2
            parts = id1.split("-")
            assert parts[0] == "EXP"
            assert len(parts[1]) == 8
            assert len(parts[2]) == 3


class TestStaleLockRecovery:
    """A lock left by a dead holder must not block every future run.

    The critical section is a sub-millisecond read-modify-write, so a lock file
    older than the staleness window cannot have a live holder. Without breaking
    it, a run killed by Ctrl+C or a crash would make EVERY later run fail with
    "could not acquire experiment-id lock" — a 10-hour experiment would die at
    startup, and the cause would look like a code bug rather than a leftover file.
    """

    def test_stale_lock_is_broken_and_id_generation_succeeds(self):
        import os
        import time

        from experiment_id import _STALE_LOCK_SECONDS

        with tempfile.TemporaryDirectory() as tmp:
            index_file = Path(tmp) / "index.json"
            lock_path = index_file.with_suffix(_LOCK_SUFFIX)
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.write_text("")  # simulate a holder that died

            old = time.time() - (_STALE_LOCK_SECONDS + 10)
            os.utime(lock_path, (old, old))

            exp_id = generate_experiment_id(index_file)
            assert exp_id.startswith("EXP-")
            assert not lock_path.exists(), "stale lock should have been removed"

    def test_fresh_lock_is_respected_not_broken(self):
        """A live holder's lock must not be stolen as stale."""
        import pytest

        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "test.lock"
            fd = _acquire_lock(lock_path)
            try:
                # A second acquire must fail while the first is held (fresh mtime,
                # so it is not treated as abandoned).
                with pytest.raises(RuntimeError, match="Could not acquire"):
                    _acquire_lock(lock_path)
            finally:
                _release_lock(fd, lock_path)
