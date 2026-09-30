# AUDIT — Integritas Data Run 50 Issue (READ-ONLY)

**Tanggal:** 2026-09-30
**Scope:** apakah hasil run 50 issue × 3 strategi = 150 run akan **JUJUR dan LENGKAP**
**Metode:** pembacaan kode + eksekusi read-only dengan strategi stub ke direktori temp (tanpa panggilan berbayar, tanpa Modal, tanpa perubahan repo)

---

## Ringkasan

| # | Temuan | Tingkat | Terbukti? |
|---|---|---|---|
| 1 | `--resume` menimpa `generation_result.csv` dan menghapus baris sesi sebelumnya | **BLOCKER** | ✅ dieksekusi |
| 2 | Instance yang di-retry dihitung DUA KALI oleh wrapper evaluasi → rate bisa >100% | **BLOCKER** | ✅ dieksekusi |
| 3 | Manifest: semua kegagalan non-TIMEOUT dilabeli `EMPTY_PATCH`, dan run penuh error terbaca `COMPLETED` | SERIUS | ✅ dieksekusi |
| 4 | `*_results.json` kehilangan PENYEBAB patch kosong (502 vs rate-limit vs model memang kosong) | SERIUS | ✅ data nyata |
| 5 | Dua rate card berbeda untuk model yang sama di hari yang sama (selisih cached 49×) | SERIUS | ✅ data nyata |
| 6 | Tidak ada pemeriksaan "jumlah diharapkan vs jumlah ada" (150) | MINOR | ✅ grep kode |
| 7 | `check_eval.py` mencari pola nama file yang sudah tidak dipakai | MINOR | ✅ kode + daftar file |
| 8 | `analyze_accuracy_vs_budget.py` menghitung cap dari log, bukan dari kolom CSV | MINOR | ✅ kode |

---

## 1. BLOCKER — `--resume` menghapus baris lama dari `generation_result.csv`

**Bukti kode:** `src/experiments/runner.py:555-558`

```python
rows = [flatten_for_csv(r) for r in all_results]   # hanya run SESI INI
df = pd.DataFrame(rows)
csv_path = f"{exp_dir}/generation_result.csv"
df.to_csv(csv_path, index=False)                   # menimpa, bukan append
```

`all_results` hanya diisi di dalam loop untuk run yang **baru dijalankan**. Instance yang di-skip karena resume (`runner.py:269-272`, `continue`) tidak pernah masuk `all_results`. Karena `to_csv` menimpa file, baris sesi sebelumnya hilang.

**Bukti eksekusi** (strategi stub, direktori temp, `experiment_id` eksplisit):

```
after first pass (3 issues):
  CSV  ids (3): ['repo__repo-1', 'repo__repo-2', 'repo__repo-3']
  JSONL ids (3): ['repo__repo-1', 'repo__repo-2', 'repo__repo-3']

after --resume with 5 issues (3 skipped, 2 newly run):
  CSV  ids (2): ['repo__repo-4', 'repo__repo-5']      <-- 3 baris HILANG
  JSONL ids (5): ['repo__repo-1', ..., 'repo__repo-5'] <-- jsonl utuh
```

**Dampak pada run 50:** skenario yang hampir pasti terjadi. `MEMORY.md` jebakan #4 dan `HANDOFF_20260929.md` §8.4 menginstruksikan `--resume` sebagai jalur pemulihan standar setelah rate limit. Setelah satu resume:

- `generation_result.csv` hanya berisi run pasca-interupsi.
- Setiap konsumen CSV melihat eksperimen yang terpotong: `report_generator.merge_data()`, `evaluation/statistics.py`, `view_results.py`, `analyze_accuracy_vs_budget.py`, `generation_report.md`, `manifest.json` (dibangun dari `all_results` yang sama).
- `experiment.yaml` juga ditulis ulang (`main.py:369`) dengan `n_issues` lengkap, jadi konfigurasi mengklaim 50 issue sementara CSV punya 20.

Data mentah **tidak hilang permanen** — `predictions/<strategy>.jsonl` memakai append (`_append_jsonl`, `runner.py:163-166`) dan tetap utuh. Tapi artefak yang dibaca tesis salah.

**Rekomendasi:** rebuild `all_results` dari jsonl di akhir run (sumber kebenaran = savepoint), bukan dari memori sesi. Alternatif minimal: sebelum `to_csv`, baca CSV lama dan gabungkan berdasarkan `(instance_id, strategy)`, dengan run terbaru menang.

---

## 2. BLOCKER — instance yang di-retry dihitung dua kali

**Bukti kode:** `tools/eval_modal.py:155-183`

```python
total_count = summary.get("submitted_instances") or len(predictions_list)
...
for pred in predictions_list:            # LIST, duplikat tidak dibuang
    inst_id = pred[KEY_INSTANCE_ID]
    if inst_id in resolved_ids:          # cabang 1: dihitung resolved
        results.append(...); resolved_count += 1
    elif inst_id in empty_ids:           # cabang 2: dihitung empty
        ...
```

`predictions_list` berasal dari `get_predictions_from_file()` (swebench `harness/utils.py:64-66`) yang mengembalikan **list mentah** tanpa dedupe. Sementara harness sendiri men-dedupe di `eval_modal.py:237-240` (`predictions_dict = {...}`), jadi harness mengevaluasi N instance unik tapi klasifikasi kita menghitung semua baris.

Skenario nyata: run mati (502) → baris error masuk jsonl. `--resume` mengulang dan **append baris kedua** untuk instance yang sama (`runner.py:512-517`). jsonl sekarang punya 2 baris untuk satu instance.

**Bukti eksekusi** (fungsi `classify_summary` asli, data sintetis):

```
predictions_list rows : 3
distinct instances    : 2
harness submitted     : 2

classify_summary -> results=3 resolved=3 total=2 empty=1 errors=0
result rows: ['django__django-10914', 'django__django-11019', 'django__django-11019']
  !! DUPLICATE instance rows in results
success_rate = resolved/total = 3/2 = 150.0%
  !! RATE ABOVE 100% -- double counting
```

Kasus satu instance: `resolved=2 total=1` → **200%**.

**Dampak pada run 50:** ini merusak angka utama tesis. Setiap instance yang pernah di-retry menambah `resolved` DAN `total`, sehingga success rate bisa melewati 100%. Karena `total_count` diambil dari `summary["submitted_instances"]` (angka harness, sudah dedupe) sementara `resolved_count` dihitung dari list mentah, pembilang dan penyebut **berasal dari dua populasi berbeda**.

**Catatan penting:** pada data yang ada di disk saat ini **tidak ditemukan duplikat** (scan semua `predictions/*.jsonl`: 0 duplikat). Jadi ini bug laten — belum merusak dataset mana pun, tapi aktif begitu `--resume` dipakai.

**Rekomendasi:** dedupe `predictions_list` berdasarkan `instance_id` sebelum klasifikasi (pertahankan baris terakhir), sehingga konsisten dengan `predictions_dict` yang dikirim ke harness. Tambahkan guard: `assert resolved_count <= total_count`.

---

## 3. SERIUS — manifest salah label dan salah status

**Bukti kode:** `src/experiments/observability.py:11-16`

```python
def _result_status(result):
    if result.patch_status == "TIMEOUT":
        return "TIMEOUT"
    if result.patch_status in ("VALID", "NORMALIZE") and result.execution.patch.strip():
        return "PATCH_GENERATED"
    return "EMPTY_PATCH"          # <-- semua sisanya jatuh ke sini
```

**Bukti eksekusi** (fungsi asli, patch tidak kosong):

```
patch_status=TIMEOUT          -> manifest status = TIMEOUT
patch_status=RATE_LIMIT       -> manifest status = EMPTY_PATCH
patch_status=PROVIDER_ERROR   -> manifest status = EMPTY_PATCH
patch_status=ERROR            -> manifest status = EMPTY_PATCH
patch_status=PARSE_ERROR      -> manifest status = EMPTY_PATCH
patch_status=TRUNCATED        -> manifest status = EMPTY_PATCH
```

Dan `observability.py:117-121`:

```python
"execution_status": (
    "COMPLETED_WITH_ERRORS" if status_counts["TIMEOUT"] > 0 else "COMPLETED"
)
```

Hanya bucket `TIMEOUT` yang diperiksa. Karena label kegagalan baru (`RATE_LIMIT`, `PROVIDER_ERROR`, `ERROR` — ditambahkan di `runner.py:493-498`) **tidak** masuk bucket TIMEOUT, sebuah run yang seluruhnya mati karena rate limit akan terbaca **`COMPLETED`**.

**Dampak pada run 50:** manifest adalah "shipping receipt". Run 150 yang dihabiskan rate limit akan menghasilkan manifest `COMPLETED` dengan 150 `EMPTY_PATCH` — tidak bisa dibedakan dari "model memang tidak menghasilkan patch 150 kali". Ini persis kelas kesalahan yang audit sebelumnya (temuan #3, "semua error dilabeli TIMEOUT") berusaha hilangkan, tapi terulang di lapisan manifest.

**Rekomendasi:** tambahkan bucket eksplisit (`RATE_LIMIT`, `PROVIDER_ERROR`, `ERROR`, `EMPTY_PATCH`, `PATCH_GENERATED`, `TIMEOUT`) dan ubah `execution_status` agar memeriksa **semua** bucket kegagalan, bukan hanya TIMEOUT.

---

## 4. SERIUS — penyebab patch kosong hilang di artefak evaluasi

**Bukti kode:** `tools/eval_modal.py:173-180`

```python
elif inst_id in empty_ids:
    row = enrich_instance_result(inst_id, False, log_dir)
    row["patch_applied"] = False
    row["failure_reason"] = "EMPTY_PATCH"     # penyebab tidak pernah disimpan
```

**Bukti data nyata** (`EXP-20260929-022`):

```
review_results.json:
  django__django-11019: {'resolved': False, 'patch_applied': False,
                         'failure_reason': 'EMPTY_PATCH'}

generation_result.csv (instance yang sama):
  patch_status=TIMEOUT
  error="Error code: 502 - {'error': {'message': '[502]: fetch failed ..."'
```

Informasi penyebab (502) ada di CSV generasi, tapi **tidak ada** di artefak evaluasi. `verify_eval_consistency.py` hanya memeriksa kecocokan label `EMPTY_PATCH`, bukan penyebabnya.

**Dampak pada run 50:** tesis perlu memisahkan "kegagalan infrastruktur" dari "model tidak menghasilkan apa-apa" untuk membenarkan eksklusi. Setelah 150 run, pertanyaan "berapa run yang mati karena provider vs karena model?" tidak bisa dijawab dari `predictions/*_results.json` sendirian. Bisa dijawab dengan join ke CSV — tapi join key `(instance_id, strategy)` rusak kalau ada duplikat (temuan 2).

**Rekomendasi:** saat mengklasifikasi, salin penyebab dari baris jsonl (`patch_status`/`error_type`) ke dalam `failure_reason`, mis. `EMPTY_PATCH:PROVIDER_502`, atau tambahkan field `empty_cause`.

---

## 5. SERIUS — dua rate card untuk model yang sama di hari yang sama

**Bukti data** (`generation_result.csv`, model `cbai/deepseek-v4.1-flash`):

| Eksperimen | `pricing_version` | Implied cached rate |
|---|---|---|
| EXP-20260930-022 | `2026-09-30-measured-from-9router-usageHistory` | **$0.002833/M** |
| EXP-20260930-023 | `...-no-cache-discount` | **$0.140000/M** |
| EXP-20260930-030 | `...-no-cache-discount` | **$0.140000/M** |

Selisih **49×** pada rate cached. Ini bukan bug kode — `cost.py:182-188` sengaja mengubah kartu setelah pengukuran menunjukkan 9router tidak memberi diskon cache. Tapi artinya biaya run sebelum dan sesudah perubahan **tidak sebanding**.

**Dampak pada run 50:** jika RQ3 mengutip biaya lintas eksperimen (atau jika run final memakai kartu berbeda dari pilot), perbandingan biaya antar-strategi tidak valid. Dalam satu eksperimen konsisten, jadi ini hanya menggigit saat pooling.

**Rekomendasi:** kunci `pricing_version` di `experiment.yaml` untuk run final dan verifikasi dengan `tools/read_actual_bill.py --compare` sebelum angka biaya dikutip.

---

## 6. MINOR — tidak ada pemeriksaan kelengkapan 150

**Bukti kode:** `src/experiments/runner.py:587-591`

```python
for strat_name in strategies:
    strat_jsonl = str(pred_dir / f"{strat_name}.jsonl")
    count = sum(1 for _ in open(strat_jsonl, ...) if _.strip())
    logger.success(f"Per-strategy predictions: {strat_jsonl} ({count} entries)")
```

`count` di-log tapi **tidak pernah** dibandingkan dengan `len(issues)`. Grep pada `runner.py` menemukan hanya 3 baris menyebut `len(issues)`/`expected`/`assert`, tidak ada yang membandingkan jumlah akhir. Tidak ada satu pun tempat di pipeline yang menegaskan "150 run ada".

**Dampak:** run yang menghasilkan 142 baris akan terlihat sukses. `check_sweep_state.py` dan `watch_curve_loop.py` membandingkan terhadap 9 (skala kecil), bukan 150.

**Rekomendasi:** setelah loop, untuk setiap strategi hitung entri yang "finished" (`_is_finished_entry`) dan bandingkan dengan `len(issues)`; log ERROR + tulis flag ke manifest bila tidak sama. Ini murah dan menutup seluruh kelas kehilangan data.

---

## 7. MINOR — `check_eval.py` mencari nama file usang

**Bukti kode:** `tools/check_eval.py:18`

```python
pattern = f"*gemini-v1-{strat}.json"
reports = list(REPORT_DIR.glob(f"**/{pattern}"))
```

Tidak ada file yang cocok: run saat ini menulis `<model>.modal-<strategy>-<EXP>.json` di **root repo**, dan model aktif adalah `cbai/deepseek-v4.1-flash` / `oc__space-bunny-free`, bukan `gemini-v1`. Tool akan mencetak "NO REPORT FOUND" untuk setiap run.

**Dampak:** kosmetik — tool mati, tidak merusak data. `tools/verify_eval_consistency.py` sudah menggantikannya.

---

## 8. MINOR — cap dihitung dari log, bukan dari CSV

**Bukti kode:** `tools/analyze_accuracy_vs_budget.py:29-46`

```python
CAP_RE = re.compile(r"hit max_tool_turns=(\d+) for role=(\w+)")
def cap_hits(exp):
    log = ROOT / f"results/{exp}/logs/experiment.log"
    ...
```

Sumber kedua untuk fakta yang sama sudah ada di CSV: kolom `truncated` dan `truncated_acts` (`csv_exporter.py:71-72`). Menurunkan cap dari log berarti (a) bergantung pada format pesan log, dan (b) tidak ikut terbawa saat `--resume` menulis ulang CSV. Belum diverifikasi apakah kedua sumber berbeda pada data nyata.

**Rekomendasi:** pakai kolom CSV (`truncated`/`truncated_acts`) sebagai sumber utama; pertahankan parsing log hanya sebagai fallback.

---

## Diperiksa dan AMAN

| Area | Hasil |
|---|---|
| **Atribusi artefak** | `runner.py:55` memakai `instance_id` langsung; tidak ada sanitasi yang bisa menabrakkan nama. ID SWE-bench hanya berisi `[a-z0-9_-]`. Tidak ada duplikat di seluruh `predictions/*.jsonl` di disk (scan penuh). |
| **Split paralel** | `tools/verify_split.py` ada dan benar: split final (seluruh repo, tidak ada repo di dua half) → 26+24=50, overlap **0**. Split yang ditolak terdeteksi: overlap 5 (`scikit-learn-10297/10508/10949`, `sympy-11400/11870`). |
| **Klasifikasi empty patch vs harness** | Kunci `empty_patch_ids` benar-benar ada di summary harness (diverifikasi pada `oc__space-bunny-free.modal-review-EXP-20260929-022.json`). Label wrapper cocok. |
| **`analyze_accuracy_vs_budget.py`** | **Sudah diperbaiki**: baris 74-78 melewati `failure_reason == "EMPTY_PATCH"`, jadi patch kosong tidak lagi masuk hitungan akurasi. (Ini menutup temuan audit sebelumnya #4.) |
| **Atribusi biaya per strategi** | Setiap baris CSV membawa kolom `strategy`; tidak ada jalur di mana biaya satu strategi tertulis ke strategi lain. Terukur pada run berbayar EXP-20260930-030: direct $0.122052, planning $0.085149, review $0.091239, total $0.298440. |
| **Lock EXP-ID** | `src/experiment_id.py` memakai `O_CREAT|O_EXCL`; dua proses paralel tidak bisa mendapat ID sama. |
| **Resume mekanisme inti** | `_is_finished_entry()` benar: baris error (patch kosong / status gagal / ada `error_type`) tidak dianggap selesai, jadi `--resume` benar-benar mengulang kegagalan. Diuji 4 test di `tests/test_resume_skips_failures.py`. |

---

## Belum bisa dipastikan

1. **Apakah temuan 2 (double-count) sudah pernah terjadi di dataset nyata.** Tidak ditemukan duplikat di disk. Karena `--resume` belum pernah dipakai pada run yang meninggalkan baris error + retry, ini murni laten. **Cara memastikan:** sebelum run 50, jalankan `python tools/verify_eval_consistency.py` dan tambahkan pemeriksaan duplikat (skrip `.audit_scan.py` pola SCAN 1) sebagai langkah preflight.

2. **Apakah kolom `truncated` CSV dan `cap_hits()` log sepakat** pada data nyata. Belum diuji. **Cara memastikan:** bandingkan keduanya pada `EXP-20260929-003` dan `EXP-20260929-022`.

3. **Apakah tagihan 9router benar-benar cocok dengan akuntansi kita** untuk run berbayar. `logs/rq3_started.json` ada (window 10:43:25Z–10:58:18Z, rc=0, 14.9 menit) dan `tools/read_actual_bill.py --compare` tersedia, tapi perbandingan belum dijalankan (butuh akses DB 9router; read-only dan murah, tapi di luar cakupan yang saya jalankan).

4. **Apakah `--resume` akan dipakai pada run 50.** Jika tim memutuskan untuk tidak pernah memakai `--resume` (mis. menjalankan ulang dari nol ke direktori baru setiap kali), temuan 1 dan 2 tidak aktif — tapi biayanya adalah mengulang run berbayar, dan jalur pemulihan rate-limit yang didokumentasikan menjadi tidak bisa dipakai.

---

## Urutan perbaikan yang disarankan

1. **Temuan 1** — rebuild CSV dari jsonl (atau merge sebelum `to_csv`). Tanpa ini, setiap resume menghasilkan artefak utama yang salah.
2. **Temuan 2** — dedupe `predictions_list` di `classify_summary` + guard `resolved <= total`. Tanpa ini, angka utama bisa >100%.
3. **Temuan 3** — bucket manifest lengkap + `execution_status` memeriksa semua kegagalan.
4. **Temuan 4** — simpan penyebab di `failure_reason`.
5. **Temuan 6** — pemeriksaan kelengkapan 150 sebagai preflight wajib.

---

## Reproduksi (semua read-only)

```bash
# Temuan 1 — CSV hilang setelah resume (stub, temp dir)
python .audit_resume_demo2.py

# Temuan 2 — double counting
python .audit_dupes.py

# Temuan 3 — pemetaan status manifest
python .audit_verify2.py

# Temuan 5 — drift rate card
python .audit_cost.py

# Kelengkapan split
python tools/verify_split.py

# Konsistensi wrapper vs harness (sudah ada)
python tools/verify_eval_consistency.py
```

Skrip `.audit_*.py` adalah scratch read-only (di-gitignore oleh `.audit_*.py`). Tidak ada file di `src/`, `tests/`, `tools/`, atau `results/` yang diubah.
