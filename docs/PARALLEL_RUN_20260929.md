# Parallel Run Readiness — 2026-09-29

**Goal:** Run 50 issues × 3 strategies in two simultaneous terminal panes (~25 instances each) and merge results.

---

## 1. EXP-ID Race Fix

**Problem:** `src/experiment_id.py` did a bare read-modify-write on `results/experiment_index.json` with no locking. Two processes starting at the same moment could both read counter=0, both write counter=1, and produce the same `EXP-YYYYMMDD-001` → directory collision.

**Fix (committed to `src/experiment_id.py`):**
- `_acquire_lock(lock_path)` opens `<index>.lock` with `O_CREAT|O_EXCL` (atomic on both POSIX and Windows NTFS).
- Retries up to 20× with 100 ms sleep if the lock already exists, so the loser waits and never sees a stale index.
- `_release_lock()` closes the fd and unlinks the lock file unconditionally (in a `finally` block).
- The critical section (read → increment → write) executes while the lock is held; the lock file is removed immediately after.

**Regression tests:** `tests/test_experiment_id_race.py`
- `test_concurrent_ids_are_unique` — 8 threads, all IDs distinct.
- `test_concurrent_counter_is_monotone` — persisted counter equals n_workers.
- `test_lock_file_removed_after_success` — no stale `.lock` after success.
- `test_lock_acquire_release` — low-level primitives work.
- `test_second_acquire_blocks_then_succeeds` — second thread waits until first releases.

---

## 2. Isolation Audit

### 2a. Repo cache — `datasets/repos/<owner>/<name>/<commit>`

Each instance maps to a **unique path** because the path encodes `repo` + `base_commit` (40-char sha). No two instances ever share a working tree unless they have the identical base commit *and* repo, which doesn't happen in the SWE-bench Lite sample (each instance is a distinct issue on a distinct commit).

Example paths for the django half (half A):
```
datasets/repos/django/django/08a4ee06510ae45562c228eefbdcaac84bd38c7a
datasets/repos/django/django/17455e924e243e7a55e8a38f45966d8cbb27c273
...  (10 distinct commits)
```
These are read-only after `get_repo_at_commit()` checks them out. The runner calls `ensure_repo_root()` per instance, which shallow-clones if missing; two processes fetching the same commit race harmlessly because `git fetch` into a fresh dir is idempotent and the path check (`if (repo_dir / ".git").exists()`) short-circuits.

**Verdict: SAFE** — no shared mutable repo state between instances.

### 2b. Results directory — `results/<EXP>/`

After the EXP-ID race fix each process receives a distinct ID before it creates any files. Everything written during a run lives under `results/<EXP>/`:
- `artifacts/<instance_id>/<strategy>/` — per instance × strategy
- `predictions/<strategy>.jsonl` — appended per completed instance
- `generation_result.csv` — appended per completed instance
- `logs/experiment.log` — per-run log (distinct path)

Two parallel runs → two distinct `results/<EXP_A>/` and `results/<EXP_B>/` trees. No cross-contamination.

**Verdict: SAFE** after the lock fix.

### 2c. Shared log — `logs/agentbench.log`

`src/utils/logger.py` adds a single Loguru sink at `logs/agentbench.log` with `rotation="5 MB"`. Both processes will append to the **same file**.

Loguru's file sink uses `open(..., mode="a")` without explicit locking. On Windows NTFS, appends from separate processes can interleave at the OS buffer level. In practice each `logger.info(...)` call writes a complete line atomically (small enough to fit in one write syscall), so lines from the two processes will be interleaved but not torn. The log is used only for human debugging, not parsed programmatically. CSV and JSONL outputs (the real data) are written to per-EXP directories and are not shared.

**Verdict: ACCEPTABLE** — interleaved log lines are readable; data outputs are not affected. If clean separation is desired, pass `--log-file results/<EXP>/logs/run.log` (not implemented yet; not required for correctness).

### 2d. `Config.TOOLCALL_REPO_DIR` sandbox

`TOOLCALL_REPO_DIR=datasets/repos` (read from `.env`). The tool loop calls `ensure_repo_root(issue)` which resolves to `datasets/repos/<repo>/<commit>`. As established in §2a this is per-commit and read-only after checkout. No sandbox collision.

### 2e. 9router (`http://localhost:20128/v1`)

See §4 below.

---

## 3. Split Plan

> ⚠️ **CORRECTION (2026-09-29, reviewed before use).** The split originally
> proposed here was **wrong and would have burned compute**. It split
> `scikit-learn=7`/`3` and `sympy=8`/`2` across the two halves. `select_issues`
> filters per repo and takes `filtered[:n]` with `seen` scoped to a single call
> (`src/dataset_loader.py:38-41`), so the SAME leading instances are selected in
> both runs. Measured with `tools/verify_split.py`: the proposed split yields
> **5 duplicate instance_ids** (`scikit-learn-10297`, `-10508`, `-10949`,
> `sympy-11400`, `-11870`) and a union of only **45**, not 50. Both halves would
> have run those 5 issues twice and the merge would have reported duplicates
> after ~10 h of compute.
>
> **Rule: a repo must not appear in both halves.** Use the whole-repo split below.

**Constraint:** All 10 django instances must be in one named half (regression set
from EXP-20260928-003). No repo may appear in both halves (see correction above).

**Half A — django + sympy + requests (26 instances):**
```
django/django=10, sympy/sympy=10, psf/requests=6
```

**Half B — scikit-learn + matplotlib + seaborn (24 instances):**
```
scikit-learn/scikit-learn=10, matplotlib/matplotlib=10, mwaskom/seaborn=4
```

Total: 26 + 24 = **50, zero overlap** (verified with `tools/verify_split.py`).

Half A is heavier (django is the slowest repo), so start it first; the 24-instance
half B will finish earlier. Both halves keep a full repo's worth of issues, so
per-repo analysis stays possible after the merge.

### Exact Commands

**Terminal pane A** (start first — heavier half):
```powershell
cd D:\development\Skripsi2\AgantBech-SE
.venv\Scripts\python.exe tools\run_with_env.py `
  --provider opencode `
  --repo-spec django/django=10 `
  --repo-spec sympy/sympy=10 `
  --repo-spec psf/requests=6
```

**Terminal pane B** (start ~5 s after pane A):
```powershell
cd D:\development\Skripsi2\AgantBech-SE
.venv\Scripts\python.exe tools\run_with_env.py `
  --provider opencode `
  --repo-spec scikit-learn/scikit-learn=10 `
  --repo-spec matplotlib/matplotlib=10 `
  --repo-spec mwaskom/seaborn=4
```

The 5 s stagger is a belt-and-suspenders precaution; the O_CREAT|O_EXCL lock
already guarantees distinct IDs even with zero stagger.

> **Before starting, confirm the split once more** (cheap, catches a typo in the
> commands above):
> ```powershell
> .venv\Scripts\python.exe tools\verify_split.py
> ```
> It must print `overlap: 0` and `union: 50`.

---

## 4. 9router Concurrency

**Endpoint:** `http://localhost:20128/v1`  
**Model:** `oc/space-bunny-free`  
**Test:** 1 / 4 / 8 simultaneous tiny requests (`max_tokens=16`, "say hi in one word")

| Concurrency | Avg latency | Max latency | Wall time | Errors |
|-------------|-------------|-------------|-----------|--------|
| 1           | 5.11 s      | 5.11 s      | 5.11 s    | 0      |
| 4           | 3.73 s      | 4.15 s      | 4.16 s    | 0      |
| 8           | 3.63 s      | 3.90 s      | 3.90 s    | 0      |

**Observation:** Latency is flat or slightly decreasing with concurrency (9router queues and batches upstream). No rate-limit errors, no timeouts. The two parallel runs each drive ~1 request per turn per instance (sequential within a run, not parallel); peak instantaneous concurrency across both panes is at most 2–3 simultaneous HTTP calls, well inside the tested safe zone.

**Verdict: SAFE** — 2 parallel runs are supported by 9router.

---

## 5. Merge Tool

**Script:** `tools/merge_experiments.py`

Merges `generation_result.csv` and `predictions/<strategy>.jsonl` from two EXP dirs into one output directory. Prints row counts per strategy and flags duplicate `instance_id`+(strategy) pairs. Supports `--dry-run`.

```powershell
# After both halves complete (EXP-A = django half, EXP-B = other half):
.venv\Scripts\python.exe tools\merge_experiments.py `
  results\EXP-20260929-001 `
  results\EXP-20260929-002 `
  results\EXP-20260929-MERGED

# Dry-run first to verify:
.venv\Scripts\python.exe tools\merge_experiments.py `
  results\EXP-20260929-001 `
  results\EXP-20260929-002 `
  results\EXP-20260929-MERGED --dry-run
```

**Tests:** `tests/test_merge_experiments.py` (5 tests).

---

## 6. Repo Cache Status

All 50 instances are already cached in `datasets/repos/`:

| Repo                     | Cached commits |
|--------------------------|----------------|
| django/django            | 10             |
| matplotlib/matplotlib    | 10             |
| mwaskom/seaborn          | 4              |
| psf/requests             | 6              |
| scikit-learn/scikit-learn| 10             |
| sympy/sympy              | 10             |
| **Total**                | **50**         |

No fetches needed. Disk usage already accounted for in existing `datasets/repos/`.

---

## Summary of Changes

| File | Change |
|------|--------|
| `src/experiment_id.py` | Added `O_CREAT\|O_EXCL` lock with retry; backward-compatible API |
| `tests/test_experiment_id_race.py` | New — 5 concurrency regression tests |
| `tools/merge_experiments.py` | New — merge CSV + per-strategy JSONL; `--dry-run` support |
| `tests/test_merge_experiments.py` | New — 5 merge correctness tests |
| `docs/PARALLEL_RUN_20260929.md` | This document |
