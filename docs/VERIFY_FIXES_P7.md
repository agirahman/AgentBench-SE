# VERIFIKASI ADVERSARIAL — perbaikan commit `ce66ea0`

**Tanggal:** 2026-09-30
**Sifat:** READ-ONLY. Tidak ada file sumber yang diubah. Skrip scratch pakai prefix `.verify_p7b_*`.
**Target:** membuktikan bahwa perbaikan di `ce66ea0` **tidak** bekerja, atau menemukan cara perbaikan itu merusak hal lain.
**Hasil test:** `323 passed` (suite penuh, 142 s). Tidak ada regresi.

---

## Ringkasan

| # | Temuan | Tingkat |
|---|---|---|
| 1 | `--resume` masih kehilangan data: crash sebelum export pertama → CSV & manifest hanya berisi run pasca-resume | **KRITIS** |
| 2 | CSV korup/terpotong → `_merge_csv_rows` membuang **SEMUA** baris lama, kembali ke bug asli | **KRITIS** |
| 3 | Budget: `review` sekarang dapat 48 turn vs `direct` 40 (+20%) — perbandingan antar-strategi tidak setara | **SERIUS** |
| 4 | `bool(NaN)` = True → baris tanpa patch ditandai "generated" pada resume kedua | **SERIUS** |
| 5 | Warning `per_task` + `REVISION_TOOL_TURNS=0` bisa jadi exception di bawah `-W error` | **MINOR** |
| 6 | `api_turns` 0 ditulis ulang jadi 1 oleh `_results_from_flat_rows` | **MINOR** |
| 7 | `patch_preview` adalah preview 100 char → manifest "rebuilt" hanya punya potongan patch | **MINOR** |

**2 KRITIS.** Keduanya adalah **lubang yang belum ditutup oleh perbaikan**, bukan bug baru yang diperkenalkan — tapi keduanya persis skenario yang perbaikan ini diklaim menyelesaikan.

---

## 🔴 KRITIS #1 — `--resume` masih kehilangan data pada crash yang sebenarnya

### Bukti kode

- `src/experiments/runner.py:711-715` — merge membaca CSV **yang ada di disk**:
  ```python
  csv_path = f"{exp_dir}/generation_result.csv"
  new_rows = [flatten_for_csv(r) for r in all_results]
  merged_rows = _merge_csv_rows(csv_path, new_rows)
  ```
- `src/experiments/runner.py:200-245` — `_merge_csv_rows` membaca `pd.read_csv(csv_path)` **hanya jika file itu ada**. Kalau tidak ada, `merged` mulai kosong dan hanya berisi `new_rows`.
- `src/experiments/runner.py:714-715` — CSV ditulis **satu kali di akhir** `run_experiments`, bukan per-run.

**Konsekuensinya:** kalau proses mati sebelum export pertama, tidak ada CSV untuk di-merge. `all_results` hanya berisi run sesi resume. CSV akhir = run pasca-resume saja.

### Bukti eksekusi (temp dir, stub, tanpa panggilan model)

`.verify_p7b_part9.py` — 5 issue, `KeyboardInterrupt` di issue 3 (Ctrl+C nyata), lalu resume dengan nama model identik:

```
session 1: CSV written? False        <- CSV ditulis SEKALI di akhir; crash = tidak ada CSV
session 1: jsonl savepoint has 2 row(s): ['django__django-1', 'django__django-2']
session 2: ran ['django__django-3', 'django__django-4', 'django__django-5']

jsonl savepoint rows : 5   (semua run tercatat)
CSV rows             : 3   ['django__django-3', 'django__django-4', 'django__django-5']
manifest processed   : 3
manifest status      : COMPLETED
INCOMPLETE.json      : False

>>> KRITIS: 2 run(s) PAID FOR BUT ABSENT from the CSV: ['django__django-1', 'django__django-2']
```

### Mengapa ini penting untuk run 50 issue

Ini justru **skenario utama** yang membuat `--resume` ada: run 6 jam mati di tengah (rate limit, listrik, Ctrl+C). Perbaikan `ce66ea0` hanya menangani kasus **"sesi pertama sempat menulis CSV"** — yang berarti sesi pertama berjalan sampai akhir. Kalau sesi pertama sudah selesai, tidak perlu resume.

Untuk run 50 issue × 3 strategi, crash di issue ke-37 berarti:
- `predictions/*.jsonl` punya 108+ baris (savepoint benar)
- `generation_result.csv` **tidak ada**
- setelah resume: CSV berisi **hanya ~42 run terakhir**
- 108 run yang sudah dibayar **hilang dari CSV dan manifest**
- `manifest.execution_status = "COMPLETED"` — tidak ada yang menandai kehilangan itu
- `INCOMPLETE.json` **tidak ditulis**, karena pengecekan completeness (`runner.py:770-777`) membaca **jsonl**, yang tetap lengkap 150/150

Jadi sistem akan melaporkan **COMPLETED** untuk sweep yang artefak utamanya kehilangan 72% datanya.

### Test yang ada tidak menangkapnya

`tests/test_resume_data_integrity.py:89-117` (`test_resume_keeps_rows_from_the_earlier_session`) menjalankan sesi pertama **sampai selesai** (3 issue selesai, CSV tertulis), lalu resume. Itu bukan crash. Nama testnya menyebut "crash-and-resume" di komentar (`baris 100`) tapi setup-nya tidak crash.

### Rekomendasi

1. Rebuild baris lama dari `predictions/predictions.jsonl` **plus** `artifacts/<id>/<strategy>/` bila CSV tidak ada atau lebih pendek dari jsonl. Savepoint adalah sumber kebenaran; CSV adalah turunan.
2. Tambahkan test: `KeyboardInterrupt` di tengah sesi pertama, lalu resume, lalu assert CSV berisi **semua** run.
3. Bandingkan jumlah baris CSV vs jumlah baris jsonl yang `_is_finished_entry`; kalau berbeda, tulis `INCOMPLETE.json`.

---

## 🔴 KRITIS #2 — CSV korup/terpotong membuang semua baris lama

### Bukti kode

`src/experiments/runner.py:226-239`:
```python
if os.path.exists(csv_path):
    try:
        old = pd.read_csv(csv_path)
        ...
        for record in old.to_dict(orient="records"):
            merged[key] = record
    except Exception as exc:  # noqa: BLE001 - never lose the new rows over this
        logger.warning(
            f"Could not read the existing CSV at {csv_path} for merging "
            f"({type(exc).__name__}: {exc}); writing this session's rows only."
        )
```

`pd.read_csv` **all-or-nothing**: satu baris rusak → `ParserError` → seluruh file dibuang. Komentar mengakui ini ("writing this session's rows only") dan menyebutnya aman karena "never lose the new rows" — tapi yang hilang adalah **baris lama**, yang justru lebih banyak.

### Bukti eksekusi

`.verify_p7b_part3.py` — CSV nyata (`EXP-20260929-022`, 9 baris) dipotong di tengah field ber-quote, yaitu yang terjadi kalau proses mati saat menulis:

```
real file: 6142 chars, 20 lines
old rows that SHOULD survive: 9
merged rows: 1
ids: ['new1']
>>> OLD ROWS LOST
```

Terpotong di batas baris juga sama: `merged rows: 1`.

WARNING yang muncul:
```
Could not read the existing CSV ... (ParserError: Error tokenizing data.
C error: EOF inside string starting at row 1); writing this session's rows only.
```

**Catatan penting:** `df.to_csv` **tidak** atomik. Proses yang mati di tengah `to_csv` meninggalkan file separuh tertulis — dan `to_csv` adalah langkah terakhir yang paling mungkin terganggu (listrik mati, OOM, Ctrl+C saat menulis 150 baris).

### Dampak

Ini mengembalikan tepat bug yang `ce66ea0` diklaim memperbaiki, dan terjadi pada **input yang lebih realistis** daripada yang ditangani: crash saat menulis, bukan crash sebelum menulis.

### Rekomendasi

1. Fallback berlapis: kalau `pd.read_csv` gagal, coba `pd.read_csv(..., on_bad_lines="skip")`, lalu parse manual baris per baris dan simpan yang valid.
2. Kalau CSV tidak terbaca, **rekonstruksi dari `predictions/*.jsonl`** alih-alih menyerah.
3. Tulis CSV secara atomik: tulis ke `generation_result.csv.tmp`, lalu `os.replace`.
4. Simpan salinan `generation_result.csv.bak` sebelum menimpa.

---

## 🟠 SERIUS #3 — `review` sekarang dapat 48 turn, `direct` 40

### Bukti kode

`src/agents/budget.py:148-154` (setelah `ce66ea0`) — `share_revision` tidak lagi kembali ke `share()` di mode `per_task`:
```python
if self.revision_reserve <= 0:
    return self.share(acts_remaining)
if self.revision_remaining <= 0:
    return 1
return max(1, self.revision_remaining // max(1, acts_remaining))
```
`src/agents/budget.py:162-172` — `spend_revision` juga tidak lagi mendebit pool base di `per_task`.

### Bukti eksekusi

`.verify_p7b_part7.py`, mereplikasi urutan pemanggilan asli setiap strategi:

```
TOTAL TURNS GRANTED PER STRATEGY
  mode=per_act   reserve=0:  direct= 40  planning= 40  review= 42   UNFAIR: review +2
  mode=per_act   reserve=8:  direct= 40  planning= 40  review= 48   UNFAIR: review +8
  mode=per_task  reserve=0:  direct= 40  planning= 40  review= 42   UNFAIR: review +2
  mode=per_task  reserve=8:  direct= 40  planning= 40  review= 48   UNFAIR: review +8
```

**Sebelum perbaikan**, `per_task` + `reserve=8` memberi review **40** (reserve diabaikan). **Sesudah**, memberi **48**.

Ini **memang perubahan yang disengaja** (memperbaiki starvation act revisi, akar yang dilaporkan MEMORY), dan `per_act` sudah berperilaku begitu sejak dulu — jadi perbaikan ini membuat kedua mode konsisten. Tapi konsekuensinya untuk tesis harus disadari:

### Dampak pada klaim RQ1

- Konfigurasi 50 issue (`.env`: `REVISION_TOOL_TURNS=8`, `BUDGET_MODE=per_act`) memberi review **48 turn**, direct & planning **40**. Review punya **20% lebih banyak turn**.
- Ini **membatalkan** klaim "every strategy gets the same total" di `docs/MEMORY.md` dan di `src/agents/budget.py:10-12` (`"gives every strategy the same TOTAL"`).
- Kurva budget level 40 & 100 yang sudah dijalankan memakai `REVISION_TOOL_TURNS=0`, jadi review di sana hanya dapat **42** (floor 1 per act revisi). Angka itu **tidak komparabel** dengan run 50 issue.
- Kalau `review` menang di run 50, pembaca bisa bertanya apakah kemenangannya berasal dari 48 vs 40 turn.

### Rekomendasi

1. Kalau tujuannya perbandingan adil: set `REVISION_TOOL_TURNS=0` dan naikkan `TOTAL_TOOL_TURNS` sehingga semua strategi dapat angka yang sama (mis. 60/60/60), atau berikan reserve ke **semua** strategi.
2. Kalau reserve dipertahankan: dokumentasikan sebagai **biaya struktural** review dan laporkan "review: 48 turn (40 base + 8 revisi)" di setiap tabel, plus jalankan analisis sensitivitas.
3. Perbaiki docstring `budget.py:10-12` — sekarang faktual salah.

---

## 🟠 SERIUS #4 — `bool(NaN)` = True: baris tanpa patch ditandai "generated"

### Bukti kode

`src/experiments/runner.py:308`:
```python
success=bool(row.get("generated")),
```

`bool(float("nan"))` bernilai `True`. Kolom `generated` menjadi NaN ketika baris lama tidak punya kolom itu, atau selnya kosong.

### Bukti eksekusi

`.verify_p7b_part6.py` dan `.verify_p7b_part8.py`:

```
# kolom ada, sel kosong
a: success=True     (generated=True)
b: success=True     (generated= KOSONG)   <-- seharusnya False
c: success=False    (generated=False)

# resume kedua: sel kosong dibaca kembali sebagai NaN
pass 1 -> old1 success=False
pass 2 -> old1 success=True   (blank cell read as [nan, True])
>>> bool(NaN) is True: a row with NO patch is marked as generated
```

### Dampak — terbatas, tapi nyata

`evaluation.success` **tidak** dibaca oleh `build_experiment_manifest` (diverifikasi dengan `inspect.getsource`: `'evaluation' referenced = False`). Jadi manifest **tidak** salah hari ini. Tapi:

- `ExperimentResult.evaluation.success` adalah field yang dibaca oleh kode lain dan oleh analisis manual.
- `_result_status` memakai `execution.patch`, bukan `evaluation.success`, jadi status manifest tetap benar.
- Risikonya adalah **field yang berbohong menunggu dibaca** — persis pola yang sudah dua kali memakan proyek ini.

### Rekomendasi

Ganti dengan pembacaan yang jujur:
```python
gen = row.get("generated")
success = bool(gen) if not pd.isna(gen) else bool(str(row.get("patch_preview") or "").strip())
```
Dan tambahkan test dengan sel kosong.

---

## 🟡 MINOR #5 — warning bisa jadi exception

`src/agents/budget.py:88-104` memanggil `warnings.warn(...)` di `__post_init__` ketika `per_task` + `reserve<=0`. Diverifikasi (`.verify_p7b_part3.py`):

```
simplefilter('error') -> RAISED UserWarning
default: warnings raised = 1
```

- `pyproject.toml` tidak punya `filterwarnings = error` (diverifikasi), jadi **aman hari ini**.
- Tapi warning ini muncul pada **setiap konstruksi** `ToolTurnBudget`, dan `run_rq3_paid.py:68` memang memakai `REVISION_TOOL_TURNS=0`. Satu `-W error` di CI, `PYTHONWARNINGS=error`, atau konfigurasi pytest di masa depan akan mengubahnya menjadi crash di tengah run 50 issue.
- Pakai `logger.warning` sekali (mis. dengan flag modul) alih-alih `warnings.warn`.

---

## 🟡 MINOR #6 — `api_turns` 0 ditulis ulang jadi 1

`src/experiments/runner.py:283`:
```python
api_turns=int(_num(row.get("total_turns"), 1)) or 1,
```

Diverifikasi (`.verify_p7b_part3.py`): `total_turns` = `0`, `0.0`, `None`, `""`, `"abc"` → semuanya jadi `api_turns=1`.

Ini mengubah run yang benar-benar memakai 0 turn menjadi 1 di manifest. `int(_num(...)) or 1` adalah idiom yang menyamakan "nol" dengan "tidak ada". Efeknya kecil (manifest `api_requests_by_strategy` naik 1 per baris semacam itu) tapi tetap salah. Terbukti nyata: `EXP-20260929-022` `django-11019/review` punya `api_turns 0` di manifest asli, dan manifest hasil rebuild melaporkan `1` (drift terdeteksi di `.verify_p7b_part2.py`, dan `api_requests_by_strategy.review` naik 128 → 129).

---

## 🟡 MINOR #7 — `patch_preview` bukan patch

`src/experiments/csv_exporter.py:115` menulis `patch_preview` = `run.patch[:100]`. `_results_from_flat_rows` memakai nilai itu sebagai `InferenceRun.patch`, dan `_result_status` memeriksanya (`result.execution.patch.strip()`).

Akibatnya baris yang direkonstruksi punya patch 100 karakter, bukan patch penuh. Untuk manifest ini tidak masalah (hanya untuk status), tapi:
- `manifest` hasil rebuild tidak bisa dipakai untuk memulihkan patch.
- Sel kosong menjadi string `'nan'` (bukan string kosong) — diverifikasi: `patch='nan' status=PATCH_GENERATED` untuk baris tanpa patch. Kombinasi dengan `_result_status` membuat baris kosong tampak punya patch.

Perbaikan: tulis kolom `patch_len` dan baca `str(...)` dengan guard `pd.isna`.

---

## ✅ Yang DIPERIKSA dan TERNYATA BENAR

1. **Merge tidak kehilangan baris pada kasus normal.** `.verify_p7b_merge.py` test A: CSV nyata 9 baris → merge dengan 0 baris baru → 9 baris, 43 kolom, tanpa kolom hilang/bertambah. Test B: nilai numerik (`total_tokens`, `execution_time`, `cost_usd_offpeak`, `total_turns`) identik setelah round-trip.
2. **Manifest hasil rebuild setara dengan manifest asli.** `.verify_p7b_part2.py` pada 3 eksperimen nyata:
   - `EXP-20260930-030`: 9/9 entri, **drift 0**, `api_requests_by_strategy` identik.
   - `EXP-20260929-003`: 9/9 entri, **drift 0**, identik.
   - `EXP-20260929-022`: 9/9 entri, drift 1 (hanya `api_turns 0→1`, lihat MINOR #6).
3. **Kolom lama yang sudah tidak dipakai tidak hilang** — `ANCIENT_COL` dipertahankan, hanya jadi NaN pada baris baru (`.verify_p7b_merge.py` test F). NaN masuk ke kolom itu, **bukan** ke kolom kunci.
4. **Urutan baris:** baris lama dulu, lalu baris baru (dict insertion order). Tidak ada konsumen yang bergantung urutan — `analyze_*.py` memakai `csv.DictReader`/`groupby`. **Tidak masalah.**
5. **CSV kosong / header-only / tanpa file:** semua ditangani tanpa crash (`.verify_p7b_merge.py` C, C2, C3).
6. **`[` di header CSV nyata:** hanya **1 dari 15** file (`EXP-20260929-003`) yang punya prefix `[[`. `lstrip("[")` menanganinya; merge mempertahankan 43 kolom. **Tidak masalah.**
7. **Test suite penuh lulus:** `323 passed` dalam 142 s, tidak ada regresi.
8. **Guard wall-clock benar-benar menghentikan act.** `tests/test_tool_loop_wall_clock.py` memakai sleep nyata dan memverifikasi loop berhenti jauh sebelum budget turn, hasilnya `truncated=True`, dan `max_wall_seconds=None`/`0` mengembalikan perilaku lama.
9. **Default 1800 s tidak akan false-positive.** Mengukur 198 run dari `results/*/generation_result.csv` (`.verify_p7b_part10.py`): hanya **2 run (1,0%)** yang totalnya melewati 1800 s — 4713 s (78,6 min) dan 2562 s (42,7 min). Karena act berjalan sekuensial, satu act >1800 s hanya mungkin pada run dengan total di atas itu. Run terpanjang yang normal adalah 1487 s (24,8 min) untuk **seluruh** run (3–5 act), jadi satu act 30 menit sangat tidak mungkin.
10. **Dedupe eval_modal memakai "baris terakhir menang" konsisten dengan harness.** `.verify_p7b_part5.py` test 1: prediksi terakhir kosong → didedupe jadi 1 baris. Perilaku ini **sama** dengan `predictions_dict` di `eval_modal.py:267-270` dan dengan dict-build harness, jadi tidak ada ketidakcocokan. **Catatan:** kasus "baris pertama sukses, baris terakhir gagal" memang akan mengevaluasi baris gagal — tapi itu juga yang dilakukan harness, jadi ini bukan regresi. Skenario ini hanya muncul kalau `--resume` me-retry instance yang sudah sukses, yang dicegah `_is_finished_entry`.
11. **Guard `resolved <= total` tidak crash pada data sah.** `.verify_p7b_part5.py` test 2: `submitted_instances` hilang → ok; `submitted_instances=0` → fallback ke `len(predictions_list)` → ok. Hanya crash kalau `resolved_ids` > `submitted_instances`, yang berarti data memang tidak konsisten. **Perilaku yang diinginkan.**
12. **`ACT_TIMEOUT_SECONDS` diteruskan ke ketiga provider** lewat default `Config` di `tool_loop.py:146-148`; tidak ada provider yang lupa.
13. **`spend_revision` di `per_task` sekarang mendebit reserve**, konsisten dengan `share_revision` (tidak ada lagi pinjaman dari pool base tanpa pengembalian).

---

## ❓ BELUM BISA DIPASTIKAN

1. **Apakah run 50 issue akan benar-benar crash sebelum export pertama.** Kalau run selesai bersih, KRITIS #1 tidak terpicu. Tidak bisa dipastikan tanpa menjalankan 6 jam. **Cara:** jalankan run kecil (3 issue) dengan `Ctrl+C` di issue ke-2, lalu resume dan periksa CSV.
2. **Apakah `df.to_csv` pernah terpotong di praktik.** Belum ada insiden terukur di `results/`. **Cara:** bungkus `to_csv` dengan penulisan atomik dan tambahkan test yang memotong file lalu memanggil `_merge_csv_rows`.
3. **Berapa banyak `finish_reason == "length"` pada run panjang** (relevan untuk `MAX_TOKENS=32768`). Tidak diukur di sini.
4. **Apakah `review` benar-benar memakai 8 turn reserve** pada run 50 issue, atau berhenti lebih awal. Bergantung perilaku model. **Cara:** `tools/check_act_budget.py` setelah run.
5. **Apakah 6 repo lain punya masalah path yang sama** dengan `django/django` (51 `.git` ditemukan, 10 kotor). Tidak diperiksa per-repo di sini.

---

## Usul urutan perbaikan

1. **KRITIS #1** — rebuild dari jsonl bila CSV tidak ada/lebih pendek. Ini yang paling mahal.
2. **KRITIS #2** — fallback parsing baris-per-baris + penulisan CSV atomik.
3. **SERIUS #3** — putuskan: setarakan budget atau dokumentasikan 48 vs 40 secara eksplisit. Perbaiki docstring `budget.py:10-12`.
4. **SERIUS #4** — ganti `bool(row.get("generated"))` dengan pembacaan yang menangani NaN.
5. **MINOR #5–7** — `logger.warning` alih-alih `warnings.warn`; hapus `or 1`; tulis `patch_len`.

**Sebelum run 50 issue:** jalankan ulang skenario crash-resume (`.verify_p7b_part9.py`) dan pastikan CSV berisi 5/5, bukan 3/5.
