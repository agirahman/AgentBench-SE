# PLAN: AgentBench-SE Update

**Tanggal**: 2026-07-17
**Branch**: 16/feat/eval-toolchain

---

## Item 1: Auto-fix Hunk Headers (swebench_adapter.py)

**Masalah**: AI sering generate hunk count mismatch (`@@ -16,27 @@` tapi body cuma 20 baris).
Sekarang: `_clean_patch()` return `""` (empty) kalau mismatch → AI dianggap gagal.

**Solusi**: Tambah `_auto_fix_hunk_headers(patch: str) -> str`:
1. Scan patch line-by-line
2. Untuk tiap hunk, hitung actual lines:
   - `M` (orig) = baris bertanda ` ` atau `-`
   - `Q` (new) = baris bertanda ` ` atau `+`
3. Rewrite header `@@ -N,M +P,Q @@` dengan angka benar
4. Keep original start line numbers (N, P)

**Integrasi** di `_clean_patch()`:
```python
def _clean_patch(text: str) -> str:
    patch = _normalize_newlines(text).strip()
    if _is_valid_patch_syntax(patch):
        return patch
    fixed = _auto_fix_hunk_headers(patch)
    if _is_valid_patch_syntax(fixed):
        logger.info("Hunk headers auto-fixed successfully")
        return fixed
    logger.warning("Patch invalid even after auto-fix")
    return ""
```

**Keuntungan skripsi**: Kontribusi engineering — "Akurasi Murni AI (X%) vs Akurasi AI setelah Auto-Fix Hunk (Y% - Meningkat Drastis)".

**Files**: `src/experiments/swebench_adapter.py`

---

## Item 2: Multi-repo Sampling 50 Issues (dataset_loader.py)

**Status saat ini**: 25 issues (requests=6, seaborn=4, django=15) — karena requests/seaborn di SWE-bench Lite cuma ada 6/4.

**New spec** (balanced 50):
```python
DEFAULT_REPO_SPECS = [
    ("django/django", 10),
    ("sympy/sympy", 10),
    ("scikit-learn/scikit-learn", 10),
    ("matplotlib/matplotlib", 10),
    ("psf/requests", 6),
    ("mwaskom/seaborn", 4),
]
# Total: 50
```

**Files**: `src/dataset_loader.py`

---

## Item 3: Difficulty Field (domain-based) (models/issue.py)

**Mapping**:
```python
DIFFICULTY_MAP = {
    "django/django": "hard",
    "sympy/sympy": "hard",
    "scikit-learn/scikit-learn": "medium",
    "matplotlib/matplotlib": "medium",
    "psf/requests": "easy",
    "mwaskom/seaborn": "easy",
}
```

**Definisi (untuk dosen)**:
- **Easy**: Utility libraries, small scope (requests, seaborn)
- **Medium**: Data science libs, moderate API (scikit-learn, matplotlib)
- **Hard**: Frameworks + symbolic compute (django, sympy)

**Implementation**:
- `Issue` model: add `difficulty` property
- `csv_exporter.py`: add `difficulty` column
- `runner.py`: log difficulty per issue
- `main.py`: log breakdown easy/medium/hard di awal

**Files**: `src/models/issue.py`, `src/experiments/csv_exporter.py`, `src/experiments/runner.py`, `src/main.py`

---

## Item 4: Enhanced Phase-level Logging (runner.py + main.py)

**Target output** (masuk ke `logs/agentbench.log` + `results/EXP-*/logs/experiment.log`):
```
[1/150] Running direct on psf__requests-1963 (easy)...
  → Process: Issue loaded (problem_statement 245 chars)
  → Process: Strategy initialized
  → Process: API call sent (model=deepseek-v4-flash)
  → Success: OK (36.3s, 6889 tokens, 1 inf, $0.0096)
  → Delay: 7.2s (random 5-10s)
```

**Detail level**:
- Per-loop: difficulty + issue stats
- Per-strategy phase: planner/executor/reviewer steps
- Per-completion: tokens, cost, time, status
- Summary: breakdown easy/medium/hard results

**Files**: `src/experiments/runner.py`, `src/main.py`

---

## Execution Order

| # | Item | Files |
|---|---|---|
| 1 | Auto-fix hunk | `swebench_adapter.py` |
| 2 | Repo 10/10/10/10/6/4 | `dataset_loader.py` |
| 3 | Difficulty field | `models/issue.py`, `csv_exporter.py` |
| 4 | Enhanced logging | `runner.py`, `main.py` |

## Verification
- `python -m py_compile` semua file
- `python -c "from main import main"` (import check)
- Dry run: `python src/main.py --provider opencode --issues 2`

## Usage
```
python src/main.py --provider opencode              # full 50 issues
python src/main.py --provider opencode --issues 5   # testing: 5 issues
python src/main.py --provider opencode --resume     # lanjut dari interupsi
```
