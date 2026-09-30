# VERIFIKASI ADVERSARIAL — Perbaikan Commit `ce66ea0` (READ-ONLY)

**Tanggal:** 2026-09-30
**Sikap:** tidak mempercayai perbaikan. Setiap klaim diuji dengan eksekusi.
**Metode:** `_merge_csv_rows` dan `_results_from_flat_rows` dipanggil langsung dari `src/experiments/runner.py`; siklus resume penuh dijalankan dengan strategi stub ke direktori temp; data nyata dibaca read-only. Tidak ada file sumber yang diubah, tidak ada evaluasi Modal.

---

## Ringkasan

| # | Temuan | Tingkat |
|---|---|---|
| 1 | Pemeriksaan kelengkapan menghitung run yang **selesai normal tanpa patch** sebagai "belum selesai" → `INCOMPLETE.json` pada run yang sehat | **KRITIS** |
| 2 | `INCOMPLETE.json` **tidak pernah dihapus** setelah resume berhasil → alarm palsu permanen | **KRITIS** |
| 3 | `api_turns` untuk run mati berubah 0 → 1 di manifest hasil rebuild (data berubah diam-diam) | SERIUS |
| 4 | `patch_preview` NaN menjadi string literal `'nan'` → lolos guard `patch.strip()` | SERIUS |
| 5 | Guard `resolved <= total` **crash** pada summary basi, setelah Modal selesai dibayar | SERIUS |
| 6 | Merge kehilangan baris pada tabrakan key (dua baris tanpa `instance_id` → 1 baris) | MINOR |
| 7 | `--resume` pada direktori yang sama dengan set issue berbeda: run yang tidak diminta ikut dihitung "hilang" | MINOR |

**Tidak ada KRITIS pada tiga perbaikan BLOCKER** (merge CSV, dedupe evaluasi, ACT_TIMEOUT) — semuanya saya uji dan berfungsi. Dua KRITIS di atas ada pada **perbaikan keempat** (pemeriksaan kelengkapan), yang justru ditambahkan agar kehilangan data terlihat.

---

## 1. KRITIS — run yang selesai normal tanpa patch dianggap "tidak selesai"

**Bukti kode:** `src/experiments/runner.py:762-768`

```python
finished_here = {
    entry.get("instance_id")
    for entry in _read_jsonl_entries(strat_jsonl)
    if _is_finished_entry(entry)     # <-- butuh patch NON-KOSONG
}
```

`_is_finished_entry` (`runner.py:113-126`) mendefinisikan "selesai" sebagai **punya patch tidak kosong**:

```python
return bool((entry.get("model_patch") or "").strip())
```

Tapi sebuah run bisa **selesai sepenuhnya** dan tetap tidak menghasilkan patch — model menjawab dengan prosa, bukan diff. Itu hasil yang sah (`patch_status: NO_DIFF` / `EMPTY`), bukan run yang hilang.

**Bukti eksekusi** (runner asli, 5 issue, 2 di antaranya selesai tanpa patch):

```
[4/5] Running direct on repo__repo-4 ... Patch empty/invalid ... recorded as empty (EMPTY)
[5/5] Running direct on repo__repo-5 ... Patch empty/invalid ... recorded as empty (EMPTY)
Completeness: 3/5 finished runs recorded -- 2 run(s) did NOT complete.
  incomplete: direct:repo__repo-4
  incomplete: direct:repo__repo-5
INCOMPLETE.json written: True
  expected=5 finished=3 missing=['direct:repo__repo-4', 'direct:repo__repo-5']
```

**Kelima issue dijalankan. Tidak ada yang di-skip, tidak ada yang hilang.** Tapi runner melaporkan ERROR dan menulis `INCOMPLETE.json`.

**Bukti pada data nyata** — `EXP-20260824-005` (150 run):

```
150 rows, 84 'unfinished' by the predicate
  infra-failure = 40    (TIMEOUT/RATE_LIMIT dengan error_type)
  legitimate-no-patch = 44   (patch_status=NO_DIFF, tanpa error_type)
```

44 dari 84 adalah run yang **selesai** dan menjawab "tidak ada diff". Predikat yang sama akan menandai semuanya sebagai belum selesai.

**Dampak pada run 50:** pada skala 150, tingkat "model menjawab tanpa diff" adalah hal biasa. Setiap run seperti itu menyalakan alarm. Karena tugas ini sendiri mencatat bahwa *"alarm palsu akan diabaikan orang, dan alarm asli ikut terabaikan"*, ini justru **melumpuhkan** kontrol yang baru ditambahkan — tepat pada run yang paling membutuhkannya.

**Ironi yang perlu dicatat:** runner sudah membedakan kedua kasus ini dengan benar di tempat lain. `observability.py` memakai `_FAILURE_STATUSES` untuk memisahkan kegagalan infrastruktur dari `EMPTY_PATCH`, dan `HANDOFF_20260929.md` §9 menegaskan *"Patch kosong ≠ patch salah. Laporkan terpisah."* Pemeriksaan kelengkapan tidak mewarisi pembedaan itu.

**Usul:** hitung "selesai" sebagai **punya baris dengan `patch_status` bukan kegagalan** (bukan "punya patch tidak kosong"):

```python
_INFRA_FAILURE = {"TIMEOUT", "RATE_LIMIT", "PROVIDER_ERROR", "ERROR", "FAILED"}
def _is_completed_entry(entry) -> bool:
    if entry.get("error_type"):
        return False
    return str(entry.get("patch_status") or "").upper() not in _INFRA_FAILURE
```

Baris `NO_DIFF`/`EMPTY` lalu dihitung selesai; hanya kematian provider yang memicu alarm. Pisahkan juga pelaporannya: `missing` (belum dijalankan) vs `failed` (dijalankan, mati).

---

## 2. KRITIS — `INCOMPLETE.json` tidak pernah dihapus

**Bukti kode:** `src/experiments/runner.py:800-812`. File ditulis **hanya** di cabang `else` (ketika tidak lengkap). Tidak ada `unlink()` di cabang sukses. Grep seluruh repo: satu-satunya penulisan adalah baris 807; **tidak ada penghapusan sama sekali**.

**Bukti eksekusi** (4 issue, 2 mati di pass 1, resume menyelesaikan semuanya):

```
PASS 1 (4 planned, 2 died)
  INCOMPLETE.json exists: True
    expected=4 finished=2 missing=['direct:repo__repo-1', 'direct:repo__repo-2']

PASS 2 (resume, all 4 finished)
  Completeness: 4/4 finished runs recorded.      <-- runner bilang LENGKAP
  INCOMPLETE.json exists: True                    <-- tapi file masih ada
    STALE CONTENT: expected=4 finished=2 complete=False
```

Runner melaporkan `4/4 finished runs recorded` sementara `INCOMPLETE.json` di direktori yang sama masih menyatakan `complete=False` dan `finished=2`.

**Dampak pada run 50:** skenario paling mungkin — rate limit memotong sweep, `INCOMPLETE.json` ditulis, lalu `--resume` menyelesaikan sisanya. Direktori akhir berisi eksperimen **lengkap** plus file bernama `INCOMPLETE.json`. Siapa pun (atau skrip preflight) yang memeriksa keberadaan file akan menolak data yang sebenarnya baik, atau — lebih buruk — mengabaikannya, dan alarm asli berikutnya ikut diabaikan. Nama file menjanjikan keadaan saat ini, tetapi isinya beku pada kegagalan pertama.

**Usul:** hapus file di cabang sukses (`Path(...).unlink(missing_ok=True)`), atau tulis selalu dengan `"complete": True/False` dan dokumentasikan bahwa **isinya** yang dibaca, bukan keberadaannya.

---

## 3. SERIUS — `api_turns` berubah 0 → 1 pada run mati

**Bukti kode:** `src/experiments/runner.py:234`

```python
api_turns=int(_num(row.get("total_turns"), 1)) or 1,
```

Untuk baris dengan `total_turns = 0`, `int(0)` → `0`, lalu `or 1` → **1**.

**Bukti eksekusi** (rebuild manifest dari CSV nyata `EXP-20260929-022`, dibandingkan dengan `manifest.json` yang ditulis kode lama dari objek memori):

```
EXP-20260929-022
  api_requests_by_strategy:
      direct: old=89  new=89
      planning: old=148  new=148
      review: old=128  new=129   <== DIFFERS
  SUM api : old=365  new=366  DIFFERS
```

Baris penyebab (`django__django-11019/review`): `inference_count=0 api_turns=1 total_turns=0 patch_status=TIMEOUT`.

**Mana yang benar?** Yang **lama** (0). Run itu tidak pernah mengirim satu pun request sukses — mati dengan 502. `csv_exporter.py:30-31` sudah benar sejak awal:

```python
total_turns = sum(...) if inferences else 0      # -> 0
first_api_turns = getattr(...) if inferences else 1   # -> 1 (untuk kolom "api_turns" legacy)
```

Manifest lama menjumlahkan `api_turns` atas `r.execution.inferences` — daftar kosong → 0. Rebuild membaca kolom `total_turns` (0) lalu `or 1` menaikkannya jadi 1.

**Dampak:** `api_requests_by_strategy` adalah metrik RQ2/RQ3 ("berapa request HTTP yang dibayar tiap strategi"). Setiap run mati menambah 1 request yang tidak pernah terjadi. Arahnya konsisten — melebihkan biaya infrastruktur. Pada 150 run dengan belasan kematian provider, ini bisa menggeser angka yang dikutip. Juga merusak janji commit: *"keeps every recorded column … faithful to what was originally measured"*.

**Usul:** hapus `or 1` — `int(_num(row.get("total_turns"), 0))`. Default 0, bukan 1, untuk run tanpa inferensi.

---

## 4. SERIUS — `patch_preview` NaN menjadi string `'nan'` yang truthy

**Bukti kode:** `src/experiments/runner.py:223`

```python
patch_preview = str(row.get("patch_preview") or "")
```

NaN bersifat **truthy** di Python, jadi `nan or ""` mengembalikan `nan`, dan `str(nan)` menghasilkan string `'nan'`.

**Bukti eksekusi:**

```
D1: NaN patch_preview -> what lands in the manifest's patch?
  review/django__django-11019: patch_status='TIMEOUT'
      execution.patch = 'nan'
      bool(patch.strip()) = True      <-- truthy!

T2: bool(nan) = True
    (nan or '') -> nan
    str(nan or '') -> 'nan'
```

**Sebaran nyata:** 41 baris di seluruh `results/` punya `patch_preview` NaN — 40 di `EXP-20260824-005`, 1 di `EXP-20260929-022`.

**Dampak:** `observability._result_status` memutuskan `PATCH_GENERATED` dengan `result.execution.patch.strip()`. String `'nan'` lolos guard itu. Hari ini tidak ada baris dengan NaN **dan** status `VALID`/`NORMALIZE` (saya periksa seluruh CSV: nol), jadi belum salah label. Tapi jaminannya rapuh: begitu satu baris punya preview kosong dengan status VALID, manifest akan melaporkan `PATCH_GENERATED` untuk patch yang tidak ada. Ini persis kelas "data terlihat masuk akal tapi salah" yang audit sebelumnya kejar.

**Usul:** `str(row.get("patch_preview") or "")` → `"" if pd.isna(row.get("patch_preview")) else str(row.get("patch_preview") or "")`.

---

## 5. SERIUS — guard bisa crash pada summary basi, setelah Modal dibayar

**Bukti kode:** `tools/eval_modal.py:200-206`

```python
if resolved_count > total_count:
    raise AssertionError(...)
```

**Bukti eksekusi:**

```
G1: resolved_ids punya id di luar predictions (5 resolved, submitted_instances=2)
  !! AssertionError: classification error: resolved=5 exceeds total=2.

G1: summary basi (3 submitted, 5 predictions setelah --resume)
  no crash: resolved=3 total=3
```

Guard **tidak** crash pada kasus basi yang saya duga (karena `resolved_count` dihitung dari predictions, bukan dari `resolved_ids`). Tapi ia crash ketika `resolved_ids` memuat id di luar predictions sementara `submitted_instances` lebih kecil.

**Dampak:** `classify_summary` dipanggil **setelah** `run_instances_modal()` selesai (`eval_modal.py:290`). Sebuah AssertionError di titik itu membuang hasil evaluasi yang sudah dibayar — tidak ada `*_results.json` yang ditulis, dan seluruh batch Modal harus dijalankan ulang. Ini menukar "angka salah yang terlihat" dengan "pekerjaan hilang", yang lebih buruk untuk run berbayar.

**Belum terbukti** apakah harness benar-benar bisa mengirim `resolved_ids` berisi id di luar predictions. Secara struktur ia membangun `submitted_ids` dari dict yang sama, jadi seharusnya konsisten — tapi saya tidak bisa membuktikannya tanpa menjalankan Modal (di luar izin).

**Usul:** ganti `raise` menjadi peringatan keras + clamp (`resolved_count = min(resolved_count, total_count)`), dan tulis `*_results.json` dengan flag `"classification_warning"`. Hasil mahal tetap tersimpan; anomali tetap terlihat.

---

## 6. MINOR — merge kehilangan baris pada tabrakan key

**Bukti kode:** `runner.py:184` dan `:193`

```python
key = (str(record.get("instance_id")), str(record.get("strategy")))
```

`str(None)` = `'None'`; `str(nan)` = `'nan'`. Dua baris tanpa `instance_id` bertabrakan pada key yang sama.

**Bukti eksekusi:**

```
2 rows in file, both with empty instance_id -> merged rows: 1
  key=(nan,'direct') tokens=222
!! ROW LOST: both rows collided on key ('nan','direct')
```

**Dampak:** rendah. `instance_id` selalu diisi oleh runner (`runner.py:389`, `:501`) dan tidak pernah kosong pada 15 CSV nyata. Ini hanya menggigit pada file yang sudah rusak. **Tidak** memengaruhi run 50.

**Usul:** lewati baris dengan `instance_id` kosong alih-alih memberinya key, atau pakai indeks baris sebagai fallback.

---

## 7. MINOR — resume dengan set issue berbeda menghitung run "hilang"

**Bukti kode:** `runner.py:770-772`

```python
for issue in issues:                       # issue dari pemanggilan INI
    if issue.instance_id not in finished_here:
        missing.append(f"{strat_name}:{issue.instance_id}")
```

`expected_total = len(issues) * len(strategies)` dihitung dari argumen pemanggilan, bukan dari konfigurasi asli eksperimen.

**Dampak:** jika direktori yang sama dipakai dengan `--repo-spec` berbeda (mis. resume dengan subset), run yang tersimpan tapi tidak diminta akan terlihat "hilang" — atau sebaliknya, `expected_total` mengecil dan shortfall nyata tersembunyi. Pada run 50 dengan perintah tetap, tidak terjadi.

**Usul:** bandingkan terhadap `len(issues)` **dan** jumlah baris unik di savepoint, dan laporkan keduanya.

---

## Yang diperiksa dan TERNYATA BENAR

**Merge CSV (`_merge_csv_rows`)** — diuji dengan 5 kelas kasus + data nyata:

| Kasus | Hasil |
|---|---|
| File tidak ada | 1 baris baru, tanpa error |
| File kosong (0 byte) | ditangani: `EmptyDataError` ditangkap, baris baru tetap ditulis |
| Hanya header | 1 baris baru, benar |
| Kolom berbeda | kedua baris selamat, selisih kolom jadi NaN (bukan pergeseran) |
| Koma / kutip / newline di `patch_preview` | bertahan utuh: `'diff --git a/x\r\n+line, with comma'` dan `'has "quotes" inside'` — **tidak ada pergeseran kolom** |
| NaN | dipertahankan sebagai NaN, tidak menjadi string |
| Instance sama, strategi beda | 2 baris terpisah (key benar) |

**Round-trip pada data nyata `EXP-20260929-003`** (merge dengan nol baris baru, lalu bandingkan sel per sel):

```
original rows: 9   merged rows: 9
original cols: 43  merged cols: 43
COLUMN NAMES identical: True
ROW-BY-ROW CELL DIFF: 0 differences
DTYPE DRIFT: none
```

**Tidak ada nilai yang berubah selain penambahan.** Ini klaim yang diminta tugas, dan hasilnya bersih.

**Klaim header `[`** — diverifikasi: **benar**. Tepat 1 dari 15 `generation_result.csv` punya header rusak (`EXP-20260929-003`, `[[instance_id,...`), dan merge **memperbaikinya** (`before: '[[instance_id,...'` → `after: 'instance_id,...'`). Bukan dead code.

**Siklus resume end-to-end** (3 issue → resume 5 issue):

```
after pass 1: 3 rows
after resume: 5 rows (expected 5)
  ids: ['repo__repo-1', ..., 'repo__repo-5']
  tokens present (not zeroed): ['110','110','110','110','110']
INCOMPLETE.json: False
```

Temuan BLOCKER #1 dari audit sebelumnya **benar-benar diperbaiki**: baris lama bertahan, dan kolom token/token biaya **tidak** di-nol-kan — kekhawatiran yang disebut commit ("trading data loss for silent data corruption") memang dihindari.

**Retry menggantikan, bukan menduplikasi:** setelah menghapus satu baris dari jsonl dan menjalankan ulang, CSV berisi 3 baris (bukan 4), dan baris yang di-retry memuat versi `SECOND` — "newest wins" bekerja.

**Dedupe evaluasi (`classify_summary`)** — kedua urutan diuji:

```
A) first=success last=empty  : resolved=1 total=1 empty=1
B) first=empty  last=success : resolved=1 total=1 empty=0
```

Tidak ada lagi 150%/200%. "Last wins" **konsisten** dengan `predictions_dict` yang dibangun harness di `eval_modal.py:237-240` dari list yang sama — jadi baris terakhir juga yang dievaluasi. Pilihan ini benar untuk kasus ini, bukan sekadar konvensi.

**Manifest rebuild — total_tokens dan cost cocok persis** pada 3 eksperimen:

| Eksperimen | tokens | cost | api_turns |
|---|---|---|---|
| EXP-20260930-030 | 2.086.879 = 2.086.879 | $0.29844 = $0.29844 | 230 = 230 |
| EXP-20260928-003 | 7.828.767 = 7.828.767 | $0.0 = $0.0 | 783 = 783 |
| EXP-20260929-022 | 6.516.972 = 6.516.972 | $0.636817 = $0.636817 | **365 ≠ 366** |

Hanya `api_turns` yang menyimpang (temuan 3), dan hanya pada baris dengan nol inferensi.

**Bucket manifest (`_result_status`)** — pemetaan sekarang benar:

```
VALID / NORMALIZE (patch ada) -> PATCH_GENERATED
TIMEOUT / RATE_LIMIT / PROVIDER_ERROR / ERROR / FAILED -> bucket masing-masing
sisanya -> EMPTY_PATCH
```

`execution_status` memeriksa **semua** bucket kegagalan, bukan hanya TIMEOUT. Temuan SERIUS audit sebelumnya diperbaiki.

**`_PATCH_STATUS_FAILED`** kini memuat `RATE_LIMIT` dan `PROVIDER_ERROR`; `_is_finished_entry` menolak baris dengan `error_type`. Run yang gagal provider **tidak** dianggap selesai — ini yang membuat pemeriksaan kelengkapan bisa bekerja (dan sekaligus penyebab temuan 1, karena predikat yang sama dipakai untuk run yang sah).

**Tool konsistensi (item 5):**

```
python tools/verify_eval_consistency.py
  RESULT: wrapper and harness agree on every level checked.
  mismatch lines: 0
```

`check_sweep_state.py` berjalan dan melaporkan level 40/100/200 seperti sebelumnya; tidak ada perubahan perilaku yang teramati. `EXP-20260929-022` tetap menampilkan `review status=TIMEOUT` dengan error 502 — pelabelan penyebab masih benar.

---

## Belum bisa dipastikan

1. **Apakah harness benar-benar bisa mengirim `resolved_ids` di luar predictions** (pemicu crash temuan 5). Butuh satu evaluasi Modal kecil untuk menguji — **di luar izin**, dan harganya tidak sebanding untuk hipotesis ini. **Cara memastikan:** jalankan satu prediksi (`--smoke`) lalu periksa apakah `submitted_ids ⊇ resolved_ids` di summary. Sementara itu, guard sebaiknya tidak `raise`.

2. **Apakah predikat kelengkapan akan menyalakan alarm pada run 50 yang sehat** (temuan 1). Saya membuktikannya pada data nyata skala 150 (`EXP-20260824-005`: 44 baris `NO_DIFF` yang sah) dan pada eksekusi stub, tapi **tidak** pada run 50 yang sesungguhnya (belum ada). **Cara memastikan:** jalankan sweep kecil (mis. 3 issue) dengan `--instance-ids` pada model murah, lalu periksa `INCOMPLETE.json` terhadap jumlah baris jsonl.

3. **Apakah `or 1` pada `api_turns` pernah mengubah angka yang dikutip.** Saya hanya membuktikan selisihnya ada pada `EXP-20260929-022` (+1 dari 365). Belum ada analisis yang saya temukan memakai `api_requests_by_strategy` secara langsung.

4. **Berapa banyak baris NaN `patch_preview` yang berasal dari run mati vs run tanpa patch.** Saya menghitung 41 baris NaN dan memastikan nol di antaranya berstatus VALID/NORMALIZE, tapi tidak menelusuri asalnya satu per satu.

---

## Catatan tentang cakupan

Dua dari tiga perbaikan BLOCKER di commit ini (`_merge_csv_rows`, dedupe evaluasi) saya uji secara agresif dan **lulus**. Perbaikan ketiga (ACT_TIMEOUT di `providers/tool_loop.py`) berada di jalur generasi dan berada di luar scope data/evaluasi yang ditugaskan, jadi tidak saya uji.

Kedua KRITIS berasal dari perbaikan keempat — pemeriksaan kelengkapan — yang ditambahkan agar kehilangan data terlihat. Perbaikannya sendiri tidak pernah menghasilkan false negative (run yang benar-benar hilang **akan** terdeteksi, saya buktikan dengan run yang gagal provider). Masalahnya adalah **false positive** pada run yang sah, plus alarm yang tidak pernah dibersihkan. Untuk run 150 yang panjang, itu cukup untuk membuat kontrol ini diabaikan.

---

## Reproduksi

```bash
# Temuan 1 — false alarm kelengkapan (runner asli, temp dir)
python .verify_incomplete.py

# Temuan 2 — INCOMPLETE.json tidak dihapus
python .verify_stale2.py

# Temuan 3 + 4 — manifest rebuild vs manifest lama pada data nyata
python .verify_manifest.py
python .verify_apiturns.py
python .verify_types.py

# Temuan 5 — guard crash
python .verify_guard.py

# Temuan 6 — tabrakan key
python .verify_stale.py

# Yang benar — merge round-trip pada data nyata
python .verify_merge2.py
python .verify_edge.py
python .verify_e2e.py
```

Skrip `.verify_*.py` adalah scratch read-only (gitignored oleh pola `.verify_*`). Tidak ada file di `src/`, `tests/`, `tools/`, atau `results/` yang diubah.
