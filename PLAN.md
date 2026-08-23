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

## Item 5: Disambiguate the Two Summary Reports

**Masalah**: Ada dua `summary.md` dengan definisi "success" berbeda yang gampang
tercampur dan salah tangkap (risiko sidang):
- `results/EXP-*/summary.md` (dari `evaluation/statistics.generate_summary_md`)
  → kolom `success` = "model mengeluarkan teks non-kosong" (PRE-EVAL, patch
  generation only, BUKAN resolved).
- `results/EXP-*/eval/summary.md` (dari `evaluation/report_generator`)
  → RQ1 dihitung dari `resolved` hasil Modal eval (INI yang defensible).

**Solusi**:
1. Di `src/evaluation/statistics.py` → `generate_summary_md`, ubah nama output
   file dari `summary.md` menjadi `generation_report.md` (atau `pre_eval_report.md`)
   agar tidak bentrok dengan laporan eval final.
2. Tambahkan header tegas di dalam file tersebut, mis.:
   `# Phase-1 Report — Patch Generation Only (NOT resolved by tests)`
   supaya pembaca tahu ini bukan klaim resolved.

**Files**: `src/evaluation/statistics.py`

---

## Item 6: Rename Misleading `success` Column

**Masalah**: Kolom `success` di `results.csv` berarti "output dihasilkan", bukan
"patch resolved". Rawan disalahartikan sebagai success rate evaluasi.

**Solusi**:
1. Rename kolom `success` → `generated` di `src/experiments/csv_exporter.py`
   (flatten_for_csv) dan di `src/evaluation/statistics.py` (`compute_success_rate`
   dipakai hanya untuk laporan generasi).
2. Pastikan `report_generator.merge_data` tetap menggunakan `resolved` (dari Modal)
   untuk RQ1 — jangan pakai kolom `generated`.
3. Update `view_results.py` kalau dia membaca kolom `success`.

**Files**: `src/experiments/csv_exporter.py`, `src/evaluation/statistics.py`,
`src/view_results.py`

---

## Item 7: Window-aware Cost (peak/off-peak) + WIB Timezone Consistency

**Context (dari docs resmi DeepSeek)**:
- Off-peak rate = setengah peak rate.
- Peak hours (UTC): `01:00–04:00` dan `06:00–10:00`. Sisa = off-peak.
- Konversi ke WIB (+7): **PEAK = 08:00–11:00 dan 13:00–17:00 WIB**;
  OFF-PEAK = 11:00–13:00 dan 17:00–08:00 (besok) WIB.
  (Peak total 7 jam/hari, off-peak 17 jam/hari — cocok dgn "off-peak half of peak".)

**Masalah A — perbandingan rate vs biaya aktual**:
`CostCalculator.calculate()` (cost.py) SUDAH hitung `cost_usd` (off-peak) DAN
`peak_total_cost_usd` (peak) secara benar. TAPI dia TIDAK pakai timestamp
inference untuk menentukan window saat eksekusi beneran terjadi. Jadi kolom
tersebut = perbandingan **TARIF**, bukan biaya aktual berdasarkan jam run.

**Masalah B — inkonsistensi timezone di pipeline**:
- Sudah benar (UTC-aware): `models/inference.py`, `models/result.py`,
  `agents/messages.py`, `experiments/observability.py`, `main.py`.
- NAIKETAN / salah:
  - `experiments/runner.py:165` → `datetime.utcnow().isoformat()` (deprecated +
    tanpa tz info).
  - `experiment_id.py:31` → `datetime.now()` (pakai waktu LOKAL mesin, bisa
    beda hari dgn artifact UTC kalau run tengah malam).

**Keputusan (HYBRID — disepakati user)**:
- **Storage tetap UTC** (standar, reproducible, no DST ambiguity).
- **Display di CSV/summary dikonversi ke WIB** lewat kolom `timestamp_wib`.
- Tambah kolom `window` (`peak`/`off_peak`) per inference berdasarkan jam WIB.

**Implementasi**:
1. `src/evaluation/cost.py`:
   - Tambah `WIB_PEAK_RANGES = [(8, 11), (13, 17)]` (jam WIB).
   - Tambah `window_for(ts_iso: str) -> str`: parse ISO, kalau naiketan
     (`tzinfo is None`) fallback `replace(tzinfo=timezone.utc)`, +7 jam → cek
     range WIB → return `"peak"` / `"off_peak"`.
   - `CostCalculator.calculate()` panggil `window_for(inference.timestamp)` lalu
     pilih rate card sesuai window (bukan selalu off lalu peak terpisah).
     Simpan juga `window` ke `CostResult`.
2. `src/experiments/csv_exporter.py`:
   - Tambah kolom `timestamp_wib` (konversi dari `result.evaluation.timestamp`)
     dan `window` (peak/off_peak) ke `flatten_for_csv`.
3. `src/experiments/runner.py:165`: ganti `datetime.utcnow()` →
   `datetime.now(timezone.utc)` (konsisten tz-aware, hindari bug window-mapping).
4. `src/experiment_id.py:31`: ganti `datetime.now()` →
   `datetime.now(timezone.utc)` supaya EXP ID pakai tanggal UTC (sejalan dgn
   artifact timestamp, hindari mismatch tengah malam).
5. `src/evaluation/statistics.py` (`_pricing_rates` / summary): tampilkan
   tarif peak DAN off-peak (bukan cuma off_peak) biar RQ3 eksplisit bandingkan
   keduanya.

**Verifikasi window**:
- 08:30 WIB → peak ✅ ; 12:00 WIB → off_peak ✅ ; 15:00 WIB → peak ✅ ;
  20:00 WIB → off_peak ✅.

**Files**: `src/evaluation/cost.py`, `src/experiments/csv_exporter.py`,
`src/experiments/runner.py`, `src/experiment_id.py`, `src/evaluation/statistics.py`

---

## Item 8: Hardening Pipeline (patch fallback + retry empty/length + resume key)

**Semua item di bawah SUDAH terverifikasi dengan membaca kode. Bukan spekulasi.**

**8a. [MEDIUM] Patch fallback false-negative (runner.py).**
- Saat ini `model_patch` di `pred_entry` (runner ~line 194) diisi dari `diff`
  hasil `extract_diff(patch.response)` — ini SUDAH benar mengekstrak field
  `"patch"` dari JSON (lihat swebench_adapter.py:253-274).
- MASALAH: kalau `extract_diff` gagal (status PARSE_ERROR / TRUNCATED /
  finish_reason="length"), `diff` kosong → `model_patch=""` → Modal eval
  bilang "no report" / patch_applied=false. Padahal `patch.response` mentah
  mungkin masih mengandung diff valid.
- Juga: `patches/{id}_{name}.txt` (runner ~line 215) disimpan dengan
  `patch.response` MENTAH, bukan `diff` → inkonsisten antara yang dikirim ke
  Modal vs yang disimpan di artifact.
- FIX:
  1. Tambah last-resort di runner: kalau `diff.strip()` kosong TAPI
     `"diff --git" in patch.response` → `model_patch = patch.response`.
  2. Simpan `patches/{id}_{name}.txt` dengan `diff` (bukan mentah) supaya
     artifact == yang dievaluasi Modal.

**8b. [MEDIUM] Retry hanya catch Exception, tidak retry empty/length.**
- `evaluation/retry.py` (`with_retry`) hanya trigger saat `Exception`
  (network/HTTP error). Kalau model balikin response kosong ATAU
  `finish_reason="length"` (patch kepotong di tengah — SERING terjadi kalau
  DeepSeek THINKING ON makan token), HTTP 200 → dianggap sukses →
  `extract_diff` balikin TRUNCATED → patch gagal.
- FIX (pilih salah satu / kombinasi):
  1. Di strategy/runner: kalau `finish_reason == "length"` atau `diff` kosong
     → panggil ulang dengan prompt "continue from where you left off" /
     naikkan `max_tokens` sementara, hingga N kali.
  2. Atau perluas `with_retry` jadi menerima predikat `should_retry(result)`
     selain Exception.

**8c. [LOW] Resume key hanya instance_id (runner.py:126).**
- `_load_existing_ids` hanya match `instance_id`. Aman selama tiap run
  menghasilkan EXP-ID BARU (folder berbeda). RISIKO hanya kalau
  `--resume` dipakai pada EXP YANG SAMA dengan konfigurasi berbeda
  (mis. reasoning on vs off) → isu "sudah done" ke-skip padahal hasil
  model/konfig lama.
- FIX (opsional): jadikan resume key = `(instance_id, strategy, model,
  thinking)` supaya aman lintas konfigurasi.

**Files**: `src/experiments/runner.py`, `src/evaluation/retry.py`

---

## Execution Order

| # | Item | Files |
|---|---|---|
| 1 | Auto-fix hunk | `swebench_adapter.py` |
| 2 | Repo 10/10/10/10/6/4 | `dataset_loader.py` |
| 3 | Difficulty field | `models/issue.py`, `csv_exporter.py` |
| 4 | Enhanced logging | `runner.py`, `main.py` |
| 5 | Disambiguate 2 summary reports | `evaluation/statistics.py` |
| 6 | Rename `success` → `generated` | `csv_exporter.py`, `statistics.py`, `view_results.py` |
| 7 | Window-aware cost + WIB timezone | `cost.py`, `csv_exporter.py`, `runner.py`, `experiment_id.py`, `statistics.py` |
| 8 | Hardening pipeline (8a/8b/8c) | `runner.py`, `retry.py` |

## Verification
- `python -m py_compile` semua file
- `python -c "from main import main"` (import check)
- Dry run: `python src/main.py --provider opencode --issues 2`
- Setelah eval Modal: `python -m src.evaluation.report_generator results/EXP-XXXXXXX`
  → cek `eval/summary.md` RQ1 pakai `resolved` (bukan `generated`)

## Usage
```
python src/main.py --provider opencode              # full 50 issues
python src/main.py --provider opencode --issues 5   # testing: 5 issues
python src/main.py --provider opencode --resume     # lanjut dari interupsi
```
